use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{self, AsyncBufReadExt, BufReader};

// --- Strict API Contracts (JSON-RPC) ---

#[derive(Deserialize, Debug)]
struct JsonRpcRequest {
    jsonrpc: String,
    id: Option<Value>,     // The client sends an ID, we must return the exact same ID
    method: String,        // e.g., "fetch_unread_emails"
    params: Option<Value>, // The specific arguments for the tool
}

#[derive(Serialize, Debug)]
struct JsonRpcResponse {
    jsonrpc: String,
    id: Option<Value>,
    
    // #[serde(skip_serializing_if = ...)] ensures that if the result is None, 
    // it doesn't show up in the final JSON string at all.
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

// --- Main Loop ---

#[tokio::main]
async fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut line = String::new();

    // CRITICAL: We can no longer use println! for debugging. 
    // The Python client reads standard output expecting STRICT JSON. 
    // Random text will break the client. Instead, we use eprintln! (Standard Error) 
    // for our logging, which the client knows to ignore.
    eprintln!("Rust MCP Server Initialized. Awaiting JSON-RPC requests...");

    loop {
        line.clear();
        
        let bytes_read = reader.read_line(&mut line).await?;
        if bytes_read == 0 {
            break; // EOF
        }

        let input = line.trim();
        if input.is_empty() {
            continue;
        }

        // 1. Parse the incoming string into our strict Request struct
        match serde_json::from_str::<JsonRpcRequest>(input) {
            Ok(request) => {
                eprintln!("Successfully parsed request for method: {}", request.method);
                
                // 2. Build a structured JSON success response
                let response = JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: request.id,
                    // For now, we mock the tool execution
                    result: Some(serde_json::json!({ 
                        "status": "success", 
                        "mock_data": format!("Pretending to run tool: {}", request.method) 
                    })),
                    error: None,
                };
                
                // 3. Serialize and send to standard output
                let response_json = serde_json::to_string(&response).unwrap();
                println!("{}", response_json);
            }
            Err(e) => {
                eprintln!("Invalid JSON received: {}", e);
                
                // If it fails to parse, we send back a strict JSON-RPC Error
                let error_response = JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id: None,
                    result: None,
                    error: Some(JsonRpcError {
                        code: -32700, // Standard JSON-RPC code for Parse Error
                        message: "Parse error: Invalid JSON structure".to_string(),
                    }),
                };
                
                let error_json = serde_json::to_string(&error_response).unwrap();
                println!("{}", error_json);
            }
        }
    }

    Ok(())
}
