mod email_client;

use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes256Gcm, Key, Nonce,
};
use axum::{
    extract::{Query, State},
    response::{
        sse::{Event, Sse},
        IntoResponse,
    },
    routing::{get, post},
    Json, Router,
};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::PgPool;
use std::{collections::HashMap, convert::Infallible, env, net::SocketAddr, sync::Arc};
use tokio::sync::{mpsc, RwLock};
use tokio_stream::wrappers::ReceiverStream;
use uuid::Uuid;

// --- Shared State for SSE Sessions ---

type SessionMap = Arc<RwLock<HashMap<String, mpsc::Sender<Result<Event, Infallible>>>>>;

#[derive(Clone)]
struct AppState {
    sessions: SessionMap,
    db: PgPool,
    encryption_key: [u8; 32],
}

// --- Strict API Contracts (JSON-RPC) ---

#[derive(Deserialize, Debug)]
struct JsonRpcRequest {
    jsonrpc: String,
    id: Option<Value>,
    method: String,
    params: Option<Value>,
}

#[derive(Serialize, Debug)]
struct JsonRpcResponse {
    jsonrpc: String,
    id: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<JsonRpcError>,
}

#[derive(Serialize, Debug)]
struct JsonRpcError {
    code: i32,
    message: String,
}

#[derive(Deserialize)]
struct SessionQuery {
    session_id: String,
}

// --- Database & Encryption Helpers ---

async fn get_credentials(
    user_id_opt: Option<&str>,
    state: &AppState,
) -> Result<(String, String), String> {
    if let Some(user_id) = user_id_opt {
        // Multi-Tenant Mode: Fetch from Postgres
        let row: Option<(String, Vec<u8>)> = sqlx::query_as("SELECT email, encrypted_password FROM users WHERE user_id = $1")
            .bind(user_id)
            .fetch_optional(&state.db)
            .await
            .map_err(|e| format!("Database error: {}", e))?;

        if let Some((email, encrypted_bytes)) = row {
            if encrypted_bytes.len() < 12 {
                return Err("Corrupted encrypted password (too short)".to_string());
            }
            let key = Key::<Aes256Gcm>::from_slice(&state.encryption_key);
            let cipher = Aes256Gcm::new(key);
            let nonce = Nonce::from_slice(&encrypted_bytes[0..12]);
            let plaintext = cipher
                .decrypt(nonce, &encrypted_bytes[12..])
                .map_err(|_| "Failed to decrypt password".to_string())?;
            let password = String::from_utf8(plaintext).map_err(|_| "Invalid UTF-8 in password".to_string())?;
            
            Ok((email, password))
        } else {
            Err(format!("User {} not found in database", user_id))
        }
    } else {
        // Legacy Single-Tenant Mode (Backwards compatibility)
        let email = env::var("IMAP_USER").map_err(|_| "Missing IMAP_USER environment variable")?;
        let pass = env::var("IMAP_PASS").map_err(|_| "Missing IMAP_PASS environment variable")?;
        Ok((email, pass))
    }
}

// --- Tool Handlers ---

async fn handle_fetch_unread_emails(params: Option<Value>, state: AppState) -> Result<Value, String> {
    let user_id = params.as_ref().and_then(|p| p.get("user_id")).and_then(Value::as_str);
    // Limit how many emails are returned per poll cycle to avoid huge SSE payloads.
    // The orchestrator calls get_email_details per-email for the full body anyway.
    let limit = params.as_ref()
        .and_then(|p| p.get("limit"))
        .and_then(Value::as_u64)
        .unwrap_or(25) as usize;

    let (username, password) = get_credentials(user_id, &state).await?;

    eprintln!("Connecting to IMAP to fetch unread emails for {} (limit={})...", username, limit);
    
    let mut session = email_client::connect(username, password).await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    let message_ids = session.search("UNSEEN").await.map_err(|e| format!("Search failed: {}", e))?;
    
    // Take the most-recent N ids (IMAP ids are ascending; sort then take from end).
    // message_ids is a HashSet so we must collect + sort before reversing.
    let total_unread = message_ids.len();
    let mut sorted_ids: Vec<u32> = message_ids.iter().copied().collect();
    sorted_ids.sort_unstable();
    let ids: Vec<u32> = sorted_ids.into_iter().rev().take(limit).collect();
    let mut emails = Vec::new();

    if !ids.is_empty() {
        // Fetch envelope only — no body — for the list view.
        // Body is fetched on demand by get_email_details.
        let seq_set = ids.iter().map(|id| id.to_string()).collect::<Vec<_>>().join(",");
        let mut fetches = session
            .fetch(&seq_set, "ENVELOPE")
            .await
            .map_err(|e| format!("Fetch failed: {}", e))?;

        while let Some(msg) = fetches.next().await {
            let msg = msg.map_err(|e| format!("Fetch message error: {}", e))?;
            let email_id = msg.message.to_string();

            let mut subject = String::new();
            let mut sender = String::new();
            let mut date = String::new();

            if let Some(env) = msg.envelope() {
                if let Some(s) = &env.subject {
                    subject = String::from_utf8_lossy(s).to_string();
                }
                if let Some(from_addrs) = &env.from {
                    if let Some(addr) = from_addrs.first() {
                        let mailbox = addr.mailbox.as_ref().map(|m| String::from_utf8_lossy(m)).unwrap_or_default();
                        let host = addr.host.as_ref().map(|h| String::from_utf8_lossy(h)).unwrap_or_default();
                        sender = format!("{}@{}", mailbox, host);
                    }
                }
                if let Some(d) = &env.date {
                    date = String::from_utf8_lossy(d).to_string();
                }
            }

            emails.push(json!({
                "id": email_id,
                "subject": subject,
                "sender": sender,
                "date": date
            }));
        }
    }
    
    eprintln!("Returning {}/{} unread emails.", emails.len(), total_unread);
    let _ = session.logout().await;

    Ok(json!({
        "status": "success",
        "emails": emails,
        "total_unread": total_unread,
        "returned": emails.len()
    }))
}

async fn handle_apply_label(params: Option<Value>, state: AppState) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let label = params.get("label").and_then(Value::as_str).ok_or("Missing 'label'")?;
    let user_id = params.get("user_id").and_then(Value::as_str);
    
    let (username, password) = get_credentials(user_id, &state).await?;

    eprintln!("Connecting to IMAP to apply label '{}' to email '{}' for {}...", label, email_id, username);
    let mut session = email_client::connect(username, password).await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    let query = format!("+FLAGS ({})", label);
    {
        let mut stream = session.store(email_id, query).await.map_err(|e| format!("Failed to apply label: {}", e))?;
        while let Some(_) = stream.next().await {}
    }
    let _ = session.logout().await;

    // Save classification log to Database
    if let Some(uid) = user_id {
        let class_data = json!({
            "action": "apply_label",
            "applied_label": label
        });
        let _ = sqlx::query("INSERT INTO email_classifications (user_id, email_id, classification_data) VALUES ($1, $2, $3)")
            .bind(uid)
            .bind(email_id)
            .bind(class_data)
            .execute(&state.db)
            .await;
    }

    Ok(json!({
        "status": "success",
        "message": format!("Label '{}' applied to email '{}'", label, email_id)
    }))
}

async fn handle_move_email(params: Option<Value>, state: AppState) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let folder = params.get("folder").and_then(Value::as_str).ok_or("Missing 'folder'")?;
    let user_id = params.get("user_id").and_then(Value::as_str);

    let (username, password) = get_credentials(user_id, &state).await?;

    eprintln!("Connecting to IMAP to move email '{}' to folder '{}' for {}...", email_id, folder, username);
    let mut session = email_client::connect(username, password).await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    session.mv(email_id, folder).await.map_err(|e| format!("Failed to move email: {}", e))?;
    
    let _ = session.logout().await;

    Ok(json!({
        "status": "success",
        "message": format!("Email '{}' moved to folder '{}'", email_id, folder)
    }))
}

async fn handle_get_email_details(params: Option<Value>, state: AppState) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let user_id = params.get("user_id").and_then(Value::as_str);

    let (username, password) = get_credentials(user_id, &state).await?;

    eprintln!("Connecting to IMAP to fetch full details for email '{}' ({})", email_id, username);
    let mut session = email_client::connect(username, password).await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;

    let mut fetches = session
        .fetch(email_id, "(ENVELOPE BODY.PEEK[TEXT] FLAGS)")
        .await
        .map_err(|e| format!("Fetch failed for email {}: {}", email_id, e))?;

    let mut email_data = None;

    if let Some(msg) = fetches.next().await {
        let msg = msg.map_err(|e| format!("Fetch message error: {}", e))?;
        
        let mut subject = String::new();
        let mut sender = String::new();
        let mut date = String::new();

        if let Some(env) = msg.envelope() {
            if let Some(s) = &env.subject {
                subject = String::from_utf8_lossy(s).to_string();
            }
            if let Some(from_addrs) = &env.from {
                if let Some(addr) = from_addrs.first() {
                    let mailbox = addr.mailbox.as_ref().map(|m| String::from_utf8_lossy(m)).unwrap_or_default();
                    let host = addr.host.as_ref().map(|h| String::from_utf8_lossy(h)).unwrap_or_default();
                    sender = format!("{}@{}", mailbox, host);
                }
            }
            if let Some(d) = &env.date {
                date = String::from_utf8_lossy(d).to_string();
            }
        }

        let body = msg
            .text()
            .map(|t| String::from_utf8_lossy(t).to_string())
            .unwrap_or_default();

        let flags: Vec<String> = msg.flags().map(|f| format!("{:?}", f)).collect();

        email_data = Some(json!({
            "id": email_id,
            "subject": subject,
            "sender": sender,
            "date": date,
            "flags": flags,
            "body": body
        }));
    }
    drop(fetches);

    let _ = session.logout().await;

    match email_data {
        Some(data) => Ok(json!({
            "status": "success",
            "email": data
        })),
        None => Err(format!("Email with ID {} not found", email_id)),
    }
}

// --- Axum Handlers ---

/// `GET /mcp` - Establishes the SSE connection.
async fn sse_handler(State(state): State<AppState>) -> Sse<ReceiverStream<Result<Event, Infallible>>> {
    let session_id = Uuid::new_v4().to_string();
    let (tx, rx) = mpsc::channel(10);

    state.sessions.write().await.insert(session_id.clone(), tx.clone());
    eprintln!("New SSE Client Connected. Session ID: {}", session_id);

    let endpoint_url = format!("/message?session_id={}", session_id);
    let init_event = Event::default().event("endpoint").data(endpoint_url);
    let _ = tx.send(Ok(init_event)).await;

    Sse::new(ReceiverStream::new(rx))
}

/// `POST /message?session_id=...` - Receives JSON-RPC commands.
async fn message_handler(
    State(state): State<AppState>,
    Query(query): Query<SessionQuery>,
    Json(request): Json<JsonRpcRequest>,
) -> impl IntoResponse {
    let session_id = query.session_id;

    let state_clone = state.clone();
    tokio::spawn(async move {
        let mut error: Option<JsonRpcError> = None;
        let mut result: Option<Value> = None;

        match request.method.as_str() {
            "tools/list" => {
                result = Some(json!({
                    "tools": [
                        {
                            "name": "fetch_unread_emails",
                            "description": "Fetches a list of unread emails from the inbox. Returns envelope metadata only (no body). Use get_email_details for full body content.",
                            "inputSchema": { 
                                "type": "object", 
                                "properties": {
                                    "user_id": { "type": "string", "description": "Optional: User ID for multi-tenant mode" },
                                    "limit": { "type": "integer", "description": "Max number of emails to return per call (default: 25, max recommended: 100)" }
                                } 
                            }
                        },
                        {
                            "name": "apply_label",
                            "description": "Applies a classification label to an email.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "email_id": { "type": "string" },
                                    "label": { "type": "string" },
                                    "user_id": { "type": "string", "description": "Optional: User ID for multi-tenant mode" }
                                },
                                "required": ["email_id", "label"]
                            }
                        },
                        {
                            "name": "move_email",
                            "description": "Moves an email to a specific folder.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "email_id": { "type": "string" },
                                    "folder": { "type": "string" },
                                    "user_id": { "type": "string", "description": "Optional: User ID for multi-tenant mode" }
                                },
                                "required": ["email_id", "folder"]
                            }
                        },
                        {
                            "name": "get_email_details",
                            "description": "Fetches full body text, envelope headers, and flags for a specific email ID.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "email_id": { "type": "string" },
                                    "user_id": { "type": "string", "description": "Optional: User ID for multi-tenant mode" }
                                },
                                "required": ["email_id"]
                            }
                        }
                    ]
                }));
            }
            "tools/call" => {
                if let Some(params) = &request.params {
                    if let Some(tool_name) = params.get("name").and_then(Value::as_str) {
                        let tool_args = params.get("arguments").cloned();
                        
                        // Helper: wrap a tool result in the MCP-compliant CallToolResult shape.
                        // The Python MCP SDK validates that `content` is present; without it
                        // a ValidationError is raised and the session is dropped.
                        let wrap = |res: Value| -> Value {
                            let text = serde_json::to_string_pretty(&res).unwrap_or_default();
                            json!({
                                "content": [{ "type": "text", "text": text }],
                                "structuredContent": res,
                                "isError": false
                            })
                        };

                        match tool_name {
                            "fetch_unread_emails" => {
                                match handle_fetch_unread_emails(tool_args, state_clone.clone()).await {
                                    Ok(res) => result = Some(wrap(res)),
                                    Err(e) => {
                                        result = Some(json!({
                                            "content": [{ "type": "text", "text": e }],
                                            "isError": true
                                        }));
                                    }
                                }
                            }
                            "apply_label" => {
                                match handle_apply_label(tool_args, state_clone.clone()).await {
                                    Ok(res) => result = Some(wrap(res)),
                                    Err(e) => {
                                        result = Some(json!({
                                            "content": [{ "type": "text", "text": e }],
                                            "isError": true
                                        }));
                                    }
                                }
                            }
                            "move_email" => {
                                match handle_move_email(tool_args, state_clone.clone()).await {
                                    Ok(res) => result = Some(wrap(res)),
                                    Err(e) => {
                                        result = Some(json!({
                                            "content": [{ "type": "text", "text": e }],
                                            "isError": true
                                        }));
                                    }
                                }
                            }
                            "get_email_details" => {
                                match handle_get_email_details(tool_args, state_clone.clone()).await {
                                    Ok(res) => result = Some(wrap(res)),
                                    Err(e) => {
                                        result = Some(json!({
                                            "content": [{ "type": "text", "text": e }],
                                            "isError": true
                                        }));
                                    }
                                }
                            }
                            _ => {
                                error = Some(JsonRpcError { code: -32601, message: format!("Tool not found: {}", tool_name) });
                            }
                        }
                    } else {
                        error = Some(JsonRpcError { code: -32602, message: "Missing tool name".to_string() });
                    }
                } else {
                    error = Some(JsonRpcError { code: -32602, message: "Missing params object".to_string() });
                }
            }

            "initialize" => {
                result = Some(json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "aguero-mcp-server",
                        "version": "0.1.0"
                    }
                }));
            }
            "notifications/initialized" => {
                // Just acknowledge, no response needed for notifications usually, 
                // but we can return an empty result to avoid error.
                result = Some(json!({}));
            }
            "ping" => {
                result = Some(json!({}));
            }
            _ => {
                error = Some(JsonRpcError { code: -32601, message: format!("Method not found: {}", request.method) });
            }
        }

        if request.id.is_some() {
            let response = JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: request.id,
                result,
                error,
            };

            if let Some(tx) = state_clone.sessions.read().await.get(&session_id) {
                let response_string = serde_json::to_string(&response).unwrap();
                let event = Event::default().event("message").data(response_string);
                if let Err(e) = tx.send(Ok(event)).await {
                    eprintln!("Failed to send SSE to {}: {}", session_id, e);
                }
            }
        }
    });

    axum::http::StatusCode::ACCEPTED
}

#[derive(Deserialize)]
struct RegisterUserPayload {
    user_id: String,
    email: String,
    app_password: String,
    settings: Option<Value>,
    metadata: Option<Value>,
}

async fn register_user_handler(
    State(state): State<AppState>,
    Json(payload): Json<RegisterUserPayload>,
) -> Result<impl IntoResponse, (axum::http::StatusCode, String)> {
    use rand::Rng;

    let key = Key::<Aes256Gcm>::from_slice(&state.encryption_key);
    let cipher = Aes256Gcm::new(key);
    
    let mut nonce_bytes = [0u8; 12];
    rand::rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, payload.app_password.as_bytes())
        .map_err(|e| (axum::http::StatusCode::INTERNAL_SERVER_ERROR, format!("Encryption failed: {}", e)))?;

    let mut encrypted_payload = nonce_bytes.to_vec();
    encrypted_payload.extend(ciphertext);

    let settings_json = payload.settings.unwrap_or(json!({}));
    let metadata_json = payload.metadata.unwrap_or(json!({}));

    sqlx::query(
        "INSERT INTO users (user_id, email, encrypted_password, settings, metadata) 
         VALUES ($1, $2, $3, $4, $5) 
         ON CONFLICT (user_id) DO UPDATE SET 
         email = EXCLUDED.email, 
         encrypted_password = EXCLUDED.encrypted_password, 
         settings = EXCLUDED.settings, 
         metadata = EXCLUDED.metadata"
    )
    .bind(&payload.user_id)
    .bind(&payload.email)
    .bind(&encrypted_payload)
    .bind(&settings_json)
    .bind(&metadata_json)
    .execute(&state.db)
    .await
    .map_err(|e| (axum::http::StatusCode::INTERNAL_SERVER_ERROR, format!("Database insert failed: {}", e)))?;

    Ok(Json(json!({
        "status": "success",
        "message": format!("User '{}' successfully registered and encrypted in PostgreSQL", payload.user_id),
        "user_id": payload.user_id
    })))
}

// --- Main Entrypoint ---

#[tokio::main]
async fn main() {
    let db_url = env::var("DATABASE_URL").unwrap_or_else(|_| "postgres://postgres:postgres@localhost:5432/mcp_db".to_string());
    
    eprintln!("Connecting to PostgreSQL database...");
    let db = PgPool::connect(&db_url).await.expect("Failed to connect to PostgreSQL");

    // Initialize Database Schema
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR PRIMARY KEY,
            email VARCHAR NOT NULL,
            encrypted_password BYTEA NOT NULL,
            settings JSONB,
            metadata JSONB
        );"
    ).execute(&db).await.expect("Failed to create users table");

    sqlx::query(
        "CREATE TABLE IF NOT EXISTS email_classifications (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            email_id VARCHAR NOT NULL,
            classification_data JSONB NOT NULL
        );"
    ).execute(&db).await.expect("Failed to create classifications table");

    // Load Encryption Key
    let key_str = env::var("ENCRYPTION_KEY").unwrap_or_else(|_| "0123456789abcdef0123456789abcdef".to_string());
    let mut encryption_key = [0u8; 32];
    let bytes = key_str.as_bytes();
    let len = bytes.len().min(32);
    encryption_key[..len].copy_from_slice(&bytes[..len]);

    let state = AppState {
        sessions: Arc::new(RwLock::new(HashMap::new())),
        db,
        encryption_key,
    };

    let app = Router::new()
        .route("/mcp", get(sse_handler))
        .route("/message", post(message_handler))
        .route("/register", post(register_user_handler))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 8080));
    eprintln!("Rust HTTP/SSE MCP Server running at http://{}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_aes_encryption_decryption_roundtrip() {
        use aes_gcm::{aead::{Aead, KeyInit}, Aes256Gcm, Key, Nonce};
        use rand::Rng;

        let mut key_bytes = [0u8; 32];
        key_bytes.copy_from_slice(b"0123456789abcdef0123456789abcdef");
        let key = Key::<Aes256Gcm>::from_slice(&key_bytes);
        let cipher = Aes256Gcm::new(key);

        let mut nonce_bytes = [0u8; 12];
        rand::rng().fill_bytes(&mut nonce_bytes);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let password = "my_secret_app_password_1234";
        let ciphertext = cipher.encrypt(nonce, password.as_bytes()).expect("encryption failed");

        let mut encrypted_payload = nonce_bytes.to_vec();
        encrypted_payload.extend(ciphertext);

        // Decrypt
        let dec_nonce = Nonce::from_slice(&encrypted_payload[0..12]);
        let plaintext = cipher.decrypt(dec_nonce, &encrypted_payload[12..]).expect("decryption failed");
        let decrypted_password = String::from_utf8(plaintext).expect("utf8 failed");

        assert_eq!(password, decrypted_password);
    }
}
