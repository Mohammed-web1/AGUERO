mod email_client;

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
use std::{collections::HashMap, convert::Infallible, net::SocketAddr, sync::Arc};
use tokio::sync::{mpsc, RwLock};
use tokio_stream::wrappers::ReceiverStream;
use uuid::Uuid;

// --- Shared State for SSE Sessions ---

type SessionMap = Arc<RwLock<HashMap<String, mpsc::Sender<Result<Event, Infallible>>>>>;

#[derive(Clone)]
struct AppState {
    sessions: SessionMap,
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

// --- Tool Handlers ---

async fn handle_fetch_unread_emails(_params: Option<Value>) -> Result<Value, String> {
    eprintln!("Connecting to IMAP to fetch unread emails...");
    
    let mut session = email_client::connect().await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    let message_ids = session.search("UNSEEN").await.map_err(|e| format!("Search failed: {}", e))?;
    
    let mut ids = Vec::new();
    for id in message_ids {
        ids.push(id);
    }
    
    eprintln!("Found {} unread emails.", ids.len());
    let _ = session.logout().await;

    Ok(json!({
        "status": "success",
        "unread_email_ids": ids
    }))
}

async fn handle_apply_label(params: Option<Value>) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let label = params.get("label").and_then(Value::as_str).ok_or("Missing 'label'")?;

    eprintln!("Connecting to IMAP to apply label '{}' to email '{}'...", label, email_id);
    let mut session = email_client::connect().await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    let query = format!("+FLAGS ({})", label);
    {
        let mut stream = session.store(email_id, query).await.map_err(|e| format!("Failed to apply label: {}", e))?;
        while let Some(_) = stream.next().await {}
    }
    
    let _ = session.logout().await;

    Ok(json!({
        "status": "success",
        "message": format!("Label '{}' applied to email '{}'", label, email_id)
    }))
}

async fn handle_move_email(params: Option<Value>) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let folder = params.get("folder").and_then(Value::as_str).ok_or("Missing 'folder'")?;

    eprintln!("Connecting to IMAP to move email '{}' to folder '{}'...", email_id, folder);
    let mut session = email_client::connect().await?;
    session.select("INBOX").await.map_err(|e| format!("Failed to select INBOX: {}", e))?;
    
    session.mv(email_id, folder).await.map_err(|e| format!("Failed to move email: {}", e))?;
    
    let _ = session.logout().await;

    Ok(json!({
        "status": "success",
        "message": format!("Email '{}' moved to folder '{}'", email_id, folder)
    }))
}

// --- Axum Handlers ---

/// `GET /mcp` - Establishes the SSE connection.
async fn sse_handler(State(state): State<AppState>) -> Sse<ReceiverStream<Result<Event, Infallible>>> {
    let session_id = Uuid::new_v4().to_string();
    let (tx, rx) = mpsc::channel(10);

    // Save the transmitter so we can send events to this client later
    state.sessions.write().await.insert(session_id.clone(), tx.clone());
    eprintln!("New SSE Client Connected. Session ID: {}", session_id);

    // Send the mandatory MCP "endpoint" event telling the client where to POST messages
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

    // We process the request asynchronously so we can return a 202 Accepted quickly
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
                            "description": "Fetches a list of unread emails from the inbox.",
                            "inputSchema": { "type": "object", "properties": {} }
                        },
                        {
                            "name": "apply_label",
                            "description": "Applies a classification label to an email.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "email_id": { "type": "string" },
                                    "label": { "type": "string" }
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
                                    "folder": { "type": "string" }
                                },
                                "required": ["email_id", "folder"]
                            }
                        }
                    ]
                }));
            }
            "tools/call" => {
                if let Some(params) = &request.params {
                    if let Some(tool_name) = params.get("name").and_then(Value::as_str) {
                        let tool_args = params.get("arguments").cloned();
                        
                        match tool_name {
                            "fetch_unread_emails" => {
                                match handle_fetch_unread_emails(tool_args).await {
                                    Ok(res) => result = Some(res),
                                    Err(e) => error = Some(JsonRpcError { code: -32603, message: e }),
                                }
                            }
                            "apply_label" => {
                                match handle_apply_label(tool_args).await {
                                    Ok(res) => result = Some(res),
                                    Err(e) => error = Some(JsonRpcError { code: -32602, message: e }),
                                }
                            }
                            "move_email" => {
                                match handle_move_email(tool_args).await {
                                    Ok(res) => result = Some(res),
                                    Err(e) => error = Some(JsonRpcError { code: -32602, message: e }),
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
            _ => {
                error = Some(JsonRpcError { code: -32601, message: format!("Method not found: {}", request.method) });
            }
        }

        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: request.id,
            result,
            error,
        };

        // Send the JSON-RPC response down the specific SSE connection
        if let Some(tx) = state_clone.sessions.read().await.get(&session_id) {
            let response_string = serde_json::to_string(&response).unwrap();
            let event = Event::default().event("message").data(response_string);
            if let Err(e) = tx.send(Ok(event)).await {
                eprintln!("Failed to send SSE to {}: {}", session_id, e);
            }
        } else {
            eprintln!("Session {} not found!", session_id);
        }
    });

    axum::http::StatusCode::ACCEPTED
}

// --- Main Entrypoint ---

#[tokio::main]
async fn main() {
    let state = AppState {
        sessions: Arc::new(RwLock::new(HashMap::new())),
    };

    let app = Router::new()
        .route("/mcp", get(sse_handler))
        .route("/message", post(message_handler))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 8080));
    eprintln!("Rust HTTP/SSE MCP Server running at http://{}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
