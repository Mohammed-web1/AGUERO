use tokio::io::{self, AsyncBufReadExt, BufReader};

#[tokio::main]
async fn main() -> io::Result<()> {
    // Set up our reader to listen to standard input (stdin)
    let stdin = io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut line = String::new();

    // A simple message to confirm the server is running.
    // Note: In a real MCP setup, we only want to output strictly valid JSON,
    // but for debugging Step 2, this is helpful!
    println!("Rust MCP Server is running! Type something and hit Enter...");

    // The Core Infinite Loop
    loop {
        line.clear();
        
        // Pause here and wait for the Python client (or you, typing in the terminal) to send a line
        let bytes_read = reader.read_line(&mut line).await?;
        
        // If 0 bytes are read, it means the connection was closed (EOF)
        if bytes_read == 0 {
            break;
        }

        let input = line.trim();
        
        // For now, we just echo it back. 
        // In the next step, we will use `serde` to parse this as JSON!
        println!("Server received: {}", input);
    }

    Ok(())
}
