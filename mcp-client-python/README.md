# Orchestrator (MCP Client)

## Purpose
This container is the brain of the operation. It is a Python-based client that uses the Model Context Protocol (MCP) to interact with the Rust server. It does not interface with the email providers directly; instead, it calls the tools provided by the Rust server.

## Workflow
1. Periodically triggers the `fetch_unread_emails` tool via the Rust server.
2. Sends the fetched content to the `ai-service-fastapi` via a standard HTTP POST request.
3. Depending on the AI's response, it triggers actions via the Rust server (e.g., `move_email` to the "Threats" folder, or `apply_label` as "Critical").

## Development Approach
Written in Python for rapid iteration and to easily parse JSON and coordinate logic.

## Configuration
Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`. Note this `.env` is only read by
`python-dotenv` for local runs outside Docker -- when run via `docker-compose`, config comes from
the `environment:` block in the repo-root `docker-compose.yml`, which itself pulls
`ANTHROPIC_API_KEY` from a separate repo-root `.env` via Compose's own variable substitution.

To run locally without Docker:
```bash
uv sync
uv run mcp-client
```

## Local end-to-end testing
`mcp-server-rust` and `ai-service-fastapi` have no implementation yet. `tests/mock_mcp_server.py`
and `tests/mock_ai_service.py` are fake stand-ins for them so this client can be run and verified
on its own:
```bash
# set ANTHROPIC_API_KEY in .env first
# run from repo root, not inside mcp-client-python/
uv run python -m mcp-client-python/tests/mock_mcp_server.py   # fake Rust server on :8080
uv run python -m mcp-client-python/tests/mock_ai_service.py   # fake AI service on :8000
uv run python -m mcp-client-python/mcp_client.py            # point .env at 127.0.0.1 for both URLs
```
