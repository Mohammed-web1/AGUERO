# Rust MCP Server (Connector)

## Purpose
This container acts as the secure, high-performance connector between the internal system and external Email Providers (Gmail, Outlook, Yahoo). It uses the Model Context Protocol (MCP) to expose specific, constrained tools to the orchestrator agent.

## Tools Exposed
- `fetch_unread_emails`: Connects via IMAP/OAuth and retrieves new messages.
- `apply_label`: Tags an email with a specific label (e.g., "Critical", "Phishing").
- `move_email`: Moves an email to a specific folder (e.g., "Quarantine").

## Development Approach
This server is written in Rust to ensure memory safety and concurrency when handling multiple API endpoints simultaneously. 

To build and test locally without Docker:
```bash
cargo build
cargo test
```
