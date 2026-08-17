# Orchestrator (MCP Client)

## Purpose
This container is the brain of the operation. It is a Python-based client that uses the Model Context Protocol (MCP) to interact with the Rust server. It does not interface with the email providers directly; instead, it calls the tools provided by the Rust server.

## Workflow
1. Periodically triggers the `fetch_unread_emails` tool via the Rust server.
2. Sends the fetched content to the `ai-service-fastapi` via a standard HTTP POST request.
3. Depending on the AI's response, it triggers actions via the Rust server (e.g., `move_email` to the "Threats" folder, or `apply_label` as "Critical").

## Development Approach
Written in Python for rapid iteration and to easily parse JSON and coordinate logic.

To run locally without Docker:
```bash
uv run src/main.py
```
