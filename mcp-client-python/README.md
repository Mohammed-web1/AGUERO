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

### MCP transport
`MCP_TRANSPORT` picks how the session to `MCP_SERVER_URL` is opened:
- `auto` (default): the SDK negotiates, which means Streamable HTTP -- a `POST` to the server URL.
  This is what `tests/mock_mcp_server.py` speaks.
- `sse`: the older HTTP+SSE transport -- `GET` the server URL for the event stream, then `POST`
  commands to the endpoint it advertises. This is what `mcp-server-rust` implements.

The SDK does not fall back from one to the other, so a server that only speaks SSE has to be named
as such. Set `MCP_TRANSPORT=sse` (as in `docker-compose.yml`) when pointing at `mcp-server-rust`.

### LLM provider
`LLM_PROVIDER` picks which model drives the apply_label/move_email decision loop:
- `anthropic` (default): Claude, with the MCP tools passed as native tool-use tools, deciding and
  calling them itself over several turns if needed. Requires `ANTHROPIC_API_KEY`.
- `ollama`: Ollama's `/api/chat`, given the email, the AI service's analysis, and the available
  tools' schemas as text, replies with structured JSON (`{"actions": [...], "reason": "..."}`),
  and the orchestrator executes each chosen tool call itself. One shot per email, no native
  tool-calling -- mirrors how `ai-service-fastapi` already talks to Ollama. Configure with
  `OLLAMA_BASE_URL` / `OLLAMA_API_KEY` / `OLLAMA_MODEL` / `OLLAMA_FORMAT_MODE` / `OLLAMA_THINK` /
  `OLLAMA_TIMEOUT_SECONDS` (see `.env.example`).

To run locally without Docker:
```bash
uv sync
uv run mcp-client
```

## Tests

```bash
uv sync
uv run pytest -q
```

No network and no running services: the MCP server is replaced by `FakeMcpSession`
(`tests/conftest.py`), and the AI service and Ollama are mocked with `respx`.

Covers the full fetch -> analyze -> act cycle (phishing quarantined, safe mail left alone, whole
batch processed), the failure paths that must not act on a mailbox (AI service down, Ollama down,
a hallucinated tool name, a tool returning `is_error`), every response shape
`unwrap_list_result` accepts, settings validation, the retry backoff, and transport selection.

## Local end-to-end testing

`mcp-server-rust` is a fully functional MCP server that implements `initialize`, `tools/list`,
and all four tool call handlers. You can run the full stack locally with `docker compose up`.
`ai-service-fastapi` can also be run directly if you want a real classification instead of the mock.

All commands below run from inside `mcp-client-python/` (set `ANTHROPIC_API_KEY`, or
`LLM_PROVIDER=ollama` plus the `OLLAMA_*` vars, in `.env` first):
```bash
uv sync

# Option A: full stack via Docker Compose (recommended)
# From the repo root:
#   docker compose up --build

# Option B: isolated local test (mock servers only)
# Terminal 1: fake Rust server (Streamable HTTP -- use MCP_TRANSPORT=auto)
uv run python tests/mock_mcp_server.py   # :8080

# Terminal 2: either the real AI service or its mock
uv run python tests/mock_ai_service.py   # :8000, fake
# -- or, from ai-service-fastapi/ instead: uvicorn app.main:app --reload

# Terminal 3: the client itself, with MCP_SERVER_URL/AI_SERVICE_URL in .env pointed at 127.0.0.1
uv run mcp-client
```
