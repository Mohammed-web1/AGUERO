# Rust MCP Server (Connector)

## Purpose
This container acts as the secure, high-performance connector between the internal system and external Email Providers (Gmail, Outlook, Yahoo). It uses the Model Context Protocol (MCP) to expose specific, constrained tools to the orchestrator agent.

## Network Architecture (HTTP/SSE)
This server runs as a standalone Docker microservice over standard HTTP port `8080`.
- **`GET /mcp`**: Establish an SSE (Server-Sent Events) connection. The server replies with an `endpoint` event containing your unique Session URL.
- **`POST /message?session_id=...`**: Post JSON-RPC 2.0 tool calls here. Responses are streamed asynchronously down the SSE channel.
- **`POST /register`**: Register a new user with their IMAP App Password. The server encrypts the password with AES-256-GCM before saving it to PostgreSQL.

### User Registration Payload Example:
```json
POST http://localhost:8080/register
Content-Type: application/json

{
  "user_id": "discord_john123",
  "email": "john@gmail.com",
  "app_password": "xxxx-yyyy-zzzz-aaaa"
}
```

## Database & Security (Multi-Tenant Mode)
This server features a "Dual-Mode" architecture:
1. **Legacy Mode (Single-User):** If no `user_id` is provided in the tool call, the server defaults to reading credentials from the `.env` file.
2. **Multi-Tenant Mode (PostgreSQL):** If a `user_id` is provided, the server queries the connected PostgreSQL database (`users` table). Passwords are encrypted in the database using **AES-256-GCM** and are dynamically decrypted in memory at runtime. Furthermore, the `apply_label` tool will automatically save a JSONB audit log of its actions to the `email_classifications` table!

## Tools Exposed & Schemas

### 1. `fetch_unread_emails`
Connects via secure IMAP and retrieves the most-recent unread messages (envelope only) from the Inbox.
Use `get_email_details` for full body content on a specific email.
- **Input:** 
  ```json
  {
    "user_id": "discord_123", // Optional. If omitted, uses .env
    "limit": 25               // Optional. Max emails per poll cycle (default: 25)
  }
  ```
- **Output:**
  ```json
  {
    "status": "success",
    "total_unread": 2173,
    "returned": 25,
    "emails": [
      {
        "id": "123",
        "subject": "Urgent Security Alert",
        "sender": "security@example.com",
        "date": "Wed, 19 Aug 2026 10:00:00 GMT"
      }
    ]
  }
  ```

### 2. `apply_label`
Tags an email with a specific IMAP flag/label.
- **Input:** 
  ```json
  {
    "user_id": "discord_123", // Optional.
    "email_id": "123",
    "label": "Important"
  }
  ```
- **Output:**
  ```json
  {
    "status": "success",
    "message": "Label 'Important' applied to email '123'"
  }
  ```

### 3. `move_email`
Moves an email to a specific IMAP folder.
- **Input:**
  ```json
  {
    "user_id": "discord_123", // Optional.
    "email_id": "123",
    "folder": "Quarantine"
  }
  ```
- **Output:**
  ```json
  {
    "status": "success",
    "message": "Email '123' moved to folder 'Quarantine'"
  }
  ```

### 4. `get_email_details`
Retrieves full body text, envelope headers, and flags for a specific email ID for deep AI threat analysis.
- **Input:**
  ```json
  {
    "user_id": "discord_123", // Optional.
    "email_id": "123"
  }
  ```
- **Output:**
  ```json
  {
    "status": "success",
    "email": {
      "id": "123",
      "subject": "Urgent Security Alert",
      "sender": "security@example.com",
      "date": "Wed, 19 Aug 2026 10:00:00 GMT",
      "flags": ["Seen"],
      "body": "Full raw email body content..."
    }
  }
  ```

## Development Approach
This server is written in Rust using `axum`, `tokio`, and `sqlx` to ensure memory safety, massive concurrency, and reliable SSE streaming.

To build and test locally:
```bash
cargo check
cargo run
```
