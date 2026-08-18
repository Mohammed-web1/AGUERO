use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{self, AsyncBufReadExt, BufReader};

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

// --- Tool Handlers ---

fn handle_fetch_unread_emails(_params: Option<Value>) -> Value {
    // In the future, this will connect to IMAP/OAuth
    json!({
        "status": "success",
        "emails": [
            { "id": "101", "subject": "Urgent: Update your password", "from": "security@fake.com" },
            { "id": "102", "subject": "50% off running shoes!", "from": "marketing@store.com" }
        ]
    })
}

fn handle_apply_label(params: Option<Value>) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let label = params.get("label").and_then(Value::as_str).ok_or("Missing 'label'")?;

    // Mock applying the label
    eprintln!("Applying label '{}' to email '{}'", label, email_id);
    
    Ok(json!({
        "status": "success",
        "message": format!("Label '{}' applied to email '{}'", label, email_id)
    }))
}

fn handle_move_email(params: Option<Value>) -> Result<Value, String> {
    let params = params.ok_or("Missing parameters")?;
    let email_id = params.get("email_id").and_then(Value::as_str).ok_or("Missing 'email_id'")?;
    let folder = params.get("folder").and_then(Value::as_str).ok_or("Missing 'folder'")?;

    // Mock moving the email
    eprintln!("Moving email '{}' to folder '{}'", email_id, folder);
    
    Ok(json!({
        "status": "success",
        "message": format!("Email '{}' moved to folder '{}'", email_id, folder)
    }))
}

// --- Main Loop ---

#[tokio::main]
async fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut line = String::new();

    eprintln!("Rust MCP Server Initialized. Awaiting JSON-RPC requests...");

    loop {
        line.clear();
        let bytes_read = reader.read_line(&mut line).await?;
        if bytes_read == 0 { break; }

        let input = line.trim();
        if input.is_empty() { continue; }

        match serde_json::from_str::<JsonRpcRequest>(input) {
            Ok(request) => {
                eprintln!("Received request: {}", request.method);
                
                let mut error: Option<JsonRpcError> = None;
                let mut result: Option<Value> = None;

                // --- The Tool Router ---
                match request.method.as_str() {
                    "tools/list" => {
                        result = Some(json!({
                            "tools": [
                                {
                                    "name": "fetch_unread_emails",
                                    "description": "Fetches a list of unread emails from the inbox.",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": { "limit": { "type": "integer" } }
                                    }
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
                                    "description": "Moves an email to a specific folder (e.g., Quarantine).",
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
                                        result = Some(handle_fetch_unread_emails(tool_args));
                                    }
                                    "apply_label" => {
                                        match handle_apply_label(tool_args) {
                                            Ok(res) => result = Some(res),
                                            Err(e) => error = Some(JsonRpcError { code: -32602, message: e }),
                                        }
                                    }
                                    "move_email" => {
                                        match handle_move_email(tool_args) {
                                            Ok(res) => result = Some(res),
                                            Err(e) => error = Some(JsonRpcError { code: -32602, message: e }),
                                        }
                                    }
                                    _ => {
                                        error = Some(JsonRpcError { code: -32601, message: format!("Tool not found: {}", tool_name) });
                                    }
                                }
                            } else {
                                error = Some(JsonRpcError { code: -32602, message: "Missing tool name in params".to_string() });
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
                
                println!("{}", serde_json::to_string(&response).unwrap());
            }
            Err(e) => {
                let error_response = JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: None,
                    result: None,
                    error: Some(JsonRpcError {
                        code: -32700,
                        message: format!("Parse error: {}", e),
                    }),
                };
                println!("{}", serde_json::to_string(&error_response).unwrap());
            }
        }
    }

    Ok(())
}
