# AGUERO — Testing Guide

> **Complete, copy-pasteable guide** for verifying every layer of the AGUERO email
> intelligence platform: from isolated unit tests to full end-to-end stack validation.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Environment Configuration](#3-environment-configuration)
4. [Full-Stack Testing (Docker Compose)](#4-full-stack-testing-docker-compose)
5. [Per-Component Isolated Testing](#5-per-component-isolated-testing)
   - 5.1 [AI Service (FastAPI)](#51-ai-service-fastapi)
   - 5.2 [MCP Client (Python Orchestrator)](#52-mcp-client-python-orchestrator)
   - 5.3 [MCP Server (Rust)](#53-mcp-server-rust)
   - 5.4 [Database (PostgreSQL)](#54-database-postgresql)
6. [Scenario-Based Verification (curl Playbook)](#6-scenario-based-verification-curl-playbook)
7. [Helper Scripts](#7-helper-scripts)
8. [Troubleshooting & Edge Cases](#8-troubleshooting--edge-cases)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Docker Network                            │
│                                                                  │
│  ┌──────────────────┐     SSE/MCP      ┌──────────────────────┐ │
│  │  mcp-client-python│ ◄────────────► │  mcp-server-rust     │ │
│  │  (Orchestrator)   │                 │  :8080               │ │
│  │  Anthropic / Ollama│               │  JSON-RPC 2.0 + SSE  │ │
│  └────────┬──────────┘                 └──────────┬───────────┘ │
│           │ POST /analyze                          │ sqlx        │
│           ▼                                        ▼             │
│  ┌──────────────────┐                 ┌──────────────────────┐  │
│  │ ai-service-fastapi│                 │  PostgreSQL :5432    │  │
│  │ :8000             │                 │  users table         │  │
│  │ Ollama + heuristics│               │  email_classifications│  │
│  └──────────────────┘                 └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

| Service              | Role                                                  | Port |
|----------------------|-------------------------------------------------------|------|
| `mcp-server-rust`    | MCP JSON-RPC 2.0 server, IMAP client, DB integration  | 8080 |
| `ai-service-fastapi` | Email classifier (Ollama LLM + heuristic fallback)    | 8000 |
| `mcp-client-python`  | Orchestrator: poll → classify → act loop              | —    |
| `postgres`           | Persistent user credentials & classification logs     | 5432 |

---

## 2. Prerequisites

### For Docker Compose (Full-Stack)
| Tool | Version | Install |
|---|---|---|
| Docker Engine | ≥ 24 | `sudo apt install docker.io` |
| Docker Compose v2 | ≥ 2.20 | `sudo apt install docker-compose-v2` |
| Git | any | `sudo apt install git` |

### For Isolated / Local Testing
| Tool | Purpose | Install |
|---|---|---|
| Rust + Cargo | Unit tests & local build | `curl https://sh.rustup.rs -sSf \| sh` |
| Python 3.11 | ai-service-fastapi & mcp-client tests | `sudo apt install python3.11` |
| `uv` (Python pkg manager) | mcp-client test runner | `pip install uv` |
| `pip` | ai-service-fastapi test runner | included with Python |
| `curl` | API smoke tests | `sudo apt install curl` |
| `psql` | Database inspection | `sudo apt install postgresql-client` |

---

## 3. Environment Configuration

### Root `.env` (required for Docker Compose)

```bash
# Copy the template, then edit with your real values
cp .env.example .env
```

| Variable | Required | Description | Example |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | If `LLM_PROVIDER=anthropic` (default) | Claude API key for the orchestrator | `sk-ant-api03-...` |
| `OLLAMA_API_KEY` | Always | Used by `ai-service-fastapi` for LLM calls | `ollama_key_...` |
| `OLLAMA_BASE_URL` | Always | Ollama API endpoint | `https://ollama.com` |
| `OLLAMA_MODEL` | Always | Model identifier | `gpt-oss:20b` |
| `IMAP_USER` | Legacy mode only | Email address (single-user fallback) | `you@gmail.com` |
| `IMAP_PASS` | Legacy mode only | Gmail App Password | `xxxx-yyyy-zzzz-aaaa` |
| `ENCRYPTION_KEY` | **Production: mandatory** | 32-char AES-256-GCM key for DB passwords | `my32charLongSecretKeyHere!!!!!` |
| `LLM_PROVIDER` | No (default: `anthropic`) | `anthropic` or `ollama` | `anthropic` |

> ⚠️ **Security**: Change `ENCRYPTION_KEY` from the default before any real deployment.
> The default `0123456789abcdef0123456789abcdef` is public and insecure.

### Per-Service `.env.example` Files
Each subdirectory has its own `.env.example` for local isolated testing:
- `ai-service-fastapi/.env.example`
- `mcp-client-python/.env.example`

---

## 4. Full-Stack Testing (Docker Compose)

### 4.1 Start the Entire System

```bash
# From the repo root
docker compose up -d --build
```

**First build takes ~2 minutes** (Rust compilation). Subsequent starts take ~10 seconds.

### 4.2 Verify All Containers Are Running

```bash
docker compose ps
```

**Expected output:**
```
NAME                 IMAGE                       STATUS
ai-service-fastapi   aguero-ai-service-fastapi   Up (healthy)
mcp-client-python    aguero-mcp-client-python    Up
mcp-server-rust      aguero-mcp-server-rust      Up
postgres             postgres:15-alpine           Up (healthy)
```

### 4.3 Health Check Endpoints

```bash
# AI Service — should return 200 with status "ok" or "degraded"
curl -s http://localhost:8000/health | python3 -m json.tool

# Rust MCP Server SSE endpoint — should return HTTP 200 and open a stream
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8080/mcp
# Expected: HTTP 200
```

**Expected `/health` response (Ollama reachable):**
```json
{
    "status": "ok",
    "model": "gpt-oss:20b",
    "ollama_base_url": "https://ollama.com",
    "ollama_reachable": true,
    "model_available": true,
    "detail": null
}
```

### 4.4 Tail Live Logs for All Services

```bash
# Watch the full orchestrator cycle
docker compose logs -f mcp-client-python

# Watch what the Rust server receives
docker compose logs -f mcp-server-rust

# Watch AI classification results
docker compose logs -f ai-service-fastapi
```

**Healthy orchestrator log pattern (no errors):**
```
INFO:httpx2:HTTP Request: GET http://mcp-server-rust:8080/mcp "HTTP/1.1 200 OK"
INFO:httpx2:HTTP Request: POST http://mcp-server-rust:8080/message?session_id=... "HTTP/1.1 202 Accepted"
```

**Rust server healthy log pattern:**
```
Connecting to IMAP to fetch unread emails for you@gmail.com (limit=25)...
IMAP Connection Successful!
Returning 25/2173 unread emails.
```

### 4.5 Restart a Single Service Without Rebuilding

```bash
docker compose restart mcp-client-python
```

### 4.6 Rebuild and Restart Only One Service

```bash
docker compose up -d --build mcp-server-rust
```

### 4.7 Shut Down

```bash
# Stop containers (data persisted in postgres_data volume)
docker compose down

# Full teardown including the database volume
docker compose down -v
```

---

## 5. Per-Component Isolated Testing

### 5.1 AI Service (FastAPI)

**Location:** `ai-service-fastapi/`
**Test runner:** `pytest` via pip
**Test files:** `tests/test_analyze.py`, `tests/test_heuristics.py`, `tests/test_normalize.py`, `tests/test_prompts.py`

#### Run All Tests

```bash
cd ai-service-fastapi
pip install -r requirements-dev.txt
pytest -v
```

**Expected output:**
```
tests/test_analyze.py::test_analyze_returns_model_verdict PASSED
tests/test_analyze.py::test_falls_back_to_heuristics_when_ollama_misbehaves[http-500] PASSED
tests/test_analyze.py::test_falls_back_to_heuristics_when_ollama_misbehaves[non-json] PASSED
...
75 passed, 1 warning in 0.51s
```

#### Run a Specific Test File

```bash
pytest tests/test_heuristics.py -v
pytest tests/test_analyze.py -v -k "phishing"
```

#### Test Coverage for Key Behaviors

| Test | What it verifies |
|---|---|
| `test_analyze_returns_model_verdict` | Ollama is called, response is forwarded as-is |
| `test_falls_back_to_heuristics_when_ollama_misbehaves` | HTTP 500 / bad JSON / off-contract enum → heuristic |
| `test_falls_back_on_timeout` | ReadTimeout → heuristic, not 503 |
| `test_returns_503_when_fallback_disabled` | `ENABLE_HEURISTIC_FALLBACK=false` → 503 |
| `test_empty_content_is_rejected` | Whitespace-only content → 422 |
| `test_an_oversized_body_is_rejected_without_calling_ollama` | >1MB body → 413 before Ollama is touched |
| `test_health_reports_degraded_when_ollama_is_down` | Ollama unreachable → 200 with `status:"degraded"` |

#### Run the Service Locally (No Docker)

```bash
cd ai-service-fastapi
pip install -r requirements.txt
OLLAMA_BASE_URL=https://ollama.com \
OLLAMA_API_KEY=your_key \
OLLAMA_MODEL=gpt-oss:20b \
uvicorn app.main:app --reload --port 8000
```

---

### 5.2 MCP Client (Python Orchestrator)

**Location:** `mcp-client-python/`
**Test runner:** `uv run pytest` (uses `uv` package manager)
**Test files:** `tests/test_client.py`, `tests/test_ai_service.py`, `tests/test_config.py`, `tests/test_mcp_tools.py`, `tests/test_ollama_decider.py`

> 💡 **Note:** Tests use `FakeMcpSession` and `respx` to mock all network calls.
> No real Anthropic key, no real MCP server, no real Ollama is needed.

#### Install Dependencies

```bash
cd mcp-client-python
uv sync
```

> If `uv` is not available: `pip install uv` first, or use `pip install -e ".[dev]"`.

#### Run All Tests (inside the container)

```bash
docker exec mcp-client-python uv run pytest -v
```

#### Run All Tests Locally

```bash
cd mcp-client-python
uv sync --all-groups
uv run pytest -v
```

#### Run a Specific Test File

```bash
uv run pytest tests/test_client.py -v
uv run pytest tests/test_client.py -v -k "phishing"
```

#### Key Test Scenarios

| Test | What it verifies |
|---|---|
| `test_phishing_email_is_labelled_and_quarantined` | Full cycle: fetch→analyze→Claude decides→apply_label+move_email called |
| `test_safe_email_is_left_alone` | Safe verdict → no action tools called |
| `test_the_rust_servers_id_only_response_still_drives_a_cycle` | Old `unread_email_ids` response shape still works |
| `test_an_empty_inbox_calls_no_action_tools` | Empty result → only `fetch_unread_emails` was called |
| `test_an_ai_service_failure_skips_only_that_email` | AI 503 on email 1 → email 2 still processed |
| `test_an_ollama_failure_takes_no_action_on_the_mailbox` | Model error → zero mailbox mutations |
| `test_a_hallucinated_tool_is_refused` | Claude invents `delete_mailbox` → it is never called |
| `test_delay_doubles_while_failing` | Backoff: 1→120s, 2→240s, 3→480s |
| `test_delay_is_capped` | Backoff ceiling is 15 minutes (900s) |

---

### 5.3 MCP Server (Rust)

**Location:** `mcp-server-rust/`
**Test runner:** `cargo test`
**Test file:** `src/main.rs` (inline `#[cfg(test)]` module)

#### Run Rust Unit Tests

```bash
cd mcp-server-rust
cargo test
```

**Expected output:**
```
running 1 test
test tests::test_aes_encryption_decryption_roundtrip ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

#### What the Unit Test Covers

| Test | What it verifies |
|---|---|
| `test_aes_encryption_decryption_roundtrip` | AES-256-GCM: encrypt a password, decrypt it, bytes match exactly |

#### Local Build Check (without Docker)

```bash
cd mcp-server-rust
cargo check     # Fast compile check (no binary produced)
cargo build     # Debug binary in ./target/debug/
```

#### Manually Test the SSE Endpoint (requires running container)

```bash
# Open the SSE stream — should immediately return the session endpoint
curl -N http://localhost:8080/mcp
```

**Expected output:**
```
event: endpoint
data: /message?session_id=<uuid>
```

#### Manually Test fetch_unread_emails via JSON-RPC

The `test_mcp.py` script at the repo root automates this:

```bash
# Requires the Docker stack to be running
python3 test_mcp.py
```

**Expected output (after ~5s for IMAP connection):**
```
[TEST] Received endpoint: /message?session_id=<uuid>
[TEST] Sending JSON-RPC POST to: http://localhost:8080/message?session_id=<uuid>
[TEST] Received RPC Response:
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}],...}}
```

---

### 5.4 Database (PostgreSQL)

The database is managed by Docker and initialised automatically on first startup via `sqlx` migrations in `mcp-server-rust`.

#### Inspect the Schema

```bash
# Connect to the running container
docker exec postgres psql -U postgres -d mcp_db

# List tables
\dt

# Expected tables:
#   users
#   email_classifications
```

#### Inspect the `users` Table

```bash
docker exec postgres psql -U postgres -d mcp_db \
  -c "SELECT user_id, email, created_at FROM users;"
```

#### Inspect the `email_classifications` Table

```bash
docker exec postgres psql -U postgres -d mcp_db \
  -c "SELECT * FROM email_classifications ORDER BY created_at DESC LIMIT 5;"
```

---

## 6. Scenario-Based Verification (curl Playbook)

> All commands below require the Docker stack to be running (`docker compose up -d`).

### Scenario 1: Register a New User

```bash
curl -s -X POST http://localhost:8080/register \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_alice",
    "email": "alice@gmail.com",
    "app_password": "xxxx-yyyy-zzzz-aaaa"
  }' | python3 -m json.tool
```

**Expected response (HTTP 200):**
```json
{
    "message": "User 'test_alice' successfully registered and encrypted in PostgreSQL",
    "status": "success",
    "user_id": "test_alice"
}
```

**Verify in DB:**
```bash
docker exec postgres psql -U postgres -d mcp_db \
  -c "SELECT user_id, email FROM users WHERE user_id='test_alice';"
```

### Scenario 2: Classify a Phishing Email

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "content": "From: security@paypa1-alerts.com\nSubject: Urgent: verify your account within 24 hours\n\nYour account will be suspended. Click here to confirm your password."
  }' | python3 -m json.tool
```

**Expected response (HTTP 200):**
```json
{
    "threat_level": "Phishing",
    "urgency": "Critical",
    "category": "Update",
    "reason": "...",
    "source": "ollama"
}
```
> If Ollama is down/unreachable, `"source"` will be `"heuristic"` — same verdict, different classifier.

### Scenario 3: Classify a Safe Work Email

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "content": "From: jane@colleague.test\nSubject: Project sync\n\nHi, let's schedule our sprint review for Friday."
  }' | python3 -m json.tool
```

**Expected response (HTTP 200):**
```json
{
    "threat_level": "Safe",
    "urgency": "Normal",
    "category": "Work",
    "source": "ollama"
}
```

### Scenario 4: Classify a Spam Email

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Subject: You won a prize! Claim your reward now. Limited time offer — act now!"
  }' | python3 -m json.tool
```

**Expected response (HTTP 200):**
```json
{
    "threat_level": "Spam",
    "urgency": "Low",
    "category": "Promotion"
}
```

### Scenario 5: Verify Heuristic Fallback (Ollama Down)

The heuristic classifier runs automatically when Ollama fails. Test it directly by sending
content that matches known phishing patterns:

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"content": "Please verify your account or it will be suspended immediately."}' \
  | python3 -m json.tool
```

**Expected `"source"` field:**
- `"ollama"` → Ollama responded correctly.
- `"heuristic"` → Ollama was down or returned an invalid response; the keyword fallback took over.

### Scenario 6: Test Input Validation — Empty Content

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"content": "   "}' \
  -o /dev/null -w "HTTP %{http_code}\n"
```

**Expected:** `HTTP 422`

### Scenario 7: Test Input Validation — Oversized Body

```bash
python3 -c "
import urllib.request, json
big_payload = json.dumps({'content': 'x' * 2_000_000}).encode()
req = urllib.request.Request(
    'http://localhost:8000/analyze',
    data=big_payload,
    headers={'Content-Type': 'application/json', 'Content-Length': str(len(big_payload))}
)
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print(f'HTTP {e.code} — {e.read().decode()}')
"
```

**Expected:** `HTTP 413 — {"detail":"Request body of ... bytes exceeds the ... byte limit"}`

### Scenario 8: MCP Tool Call — fetch_unread_emails via SSE

```bash
# Requires the stack to be running with valid IMAP credentials in .env
python3 test_mcp.py
```

**Expected:** The script connects, sends `tools/call fetch_unread_emails`, and prints the
JSON-RPC response containing the 25 most-recent unread email envelopes.

---

## 7. Helper Scripts

### `register_user.py` — Register a multi-tenant user

```bash
python3 register_user.py <user_id> <email> <app_password>

# Example:
python3 register_user.py alice alice@gmail.com xxxx-yyyy-zzzz-aaaa
```

**Requires:** `mcp-server-rust` running (port 8080 accessible).

### `test_mcp.py` — Raw SSE/JSON-RPC smoke test

```bash
python3 test_mcp.py
```

**What it does:**
1. Connects to `http://localhost:8080/mcp` (SSE stream).
2. Reads the session endpoint from the `data:` event.
3. Sends a `tools/call fetch_unread_emails` JSON-RPC POST.
4. Prints the response from the SSE stream.

---

## 8. Troubleshooting & Edge Cases

### ❌ `mcp-server-rust` fails to start — "no ENCRYPTION_KEY"

**Symptom:** Container exits immediately.
**Fix:** Ensure `ENCRYPTION_KEY` is set to a 32-character string in `.env`.
```bash
# Generate a secure key
python3 -c "import secrets; print(secrets.token_hex(16))"
```

### ❌ `mcp-client-python` logs `MCPError: Connection closed`

**Symptom:** Orchestrator repeatedly fails with `Poll cycle failed (N in a row)`.  
**Cause:** The IMAP inbox has too many unread emails and the response exceeds SSE buffer.  
**Fix:** Use the `limit` parameter or reduce the inbox size. The default is capped at 25 emails per cycle.

### ❌ `mcp-client-python` logs `anthropic.AuthenticationError: invalid x-api-key`

**Cause:** `ANTHROPIC_API_KEY` in `.env` is a placeholder.  
**Fix:** Set your real Anthropic API key, then restart:
```bash
docker compose restart mcp-client-python
```

### ❌ `ai-service-fastapi` reports `"status": "degraded"`

**Cause:** Ollama is unreachable or the model is not pulled.  
**Impact:** None — heuristic fallback automatically activates. Classification still works.  
**To fix (if model not pulled):** Pull the model via the Ollama API or dashboard.

### ❌ Port 5432 already in use

**Symptom:** `bind: address already in use` for port 5432.  
**Fix:** A local PostgreSQL instance is running. Stop it:
```bash
sudo systemctl stop postgresql
# or, to only expose PostgreSQL inside Docker (remove host port binding):
# In docker-compose.yml, change:  ports: ["5432:5432"]  ->  remove the ports block
```

### ❌ Port 8080 or 8000 already in use

```bash
# Find the conflicting process
sudo lsof -i :8080
sudo lsof -i :8000

# Kill it
sudo kill -9 <PID>
```

### ❌ `cargo test` fails — `E0277: trait bound not satisfied`

**Context:** Occurs when using `.rev()` on a `HashSet` iterator directly.  
**Fix:** Already patched — `HashSet` is sorted into a `Vec` before reversing. Ensure you have
the latest code with `git pull`.

### ❌ `uv: command not found` when running mcp-client tests locally

```bash
pip install uv
# Then retry:
uv sync && uv run pytest -v
```

### ❌ `ValidationError: content Field required` in mcp-client-python logs

**Context:** Old version of `mcp-server-rust` returned raw JSON instead of the MCP
`CallToolResult` envelope.  
**Fix:** Ensure the latest `mcp-server-rust` is running. Rebuild if needed:
```bash
docker compose up -d --build mcp-server-rust
```

### ❌ Docker image build fails — OOM during `cargo install`

**Context:** On machines with <2 GB RAM (e.g., GCP `e2-micro`).  
**Fix:** Add a swap file before building:
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
docker compose up -d --build
```

### ❌ `notifications/initialized` causes a JSON-RPC response error

**Context:** The MCP protocol forbids replying to notification messages (requests without `id`).  
**Fix:** Already patched in `mcp-server-rust`. Notifications are acknowledged internally but
no JSON-RPC response is sent back over SSE.

---

## Quick Reference

```bash
# Start everything
docker compose up -d --build

# Check health
curl -s http://localhost:8000/health | python3 -m json.tool
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8080/mcp

# Run all unit tests
cd ai-service-fastapi && pytest -v          # 75 tests
cd mcp-server-rust && cargo test            # 1 test
cd mcp-client-python && uv run pytest -v   # ~20 tests

# Register a user
python3 register_user.py alice alice@gmail.com xxxx-yyyy-zzzz-aaaa

# Raw MCP smoke test
python3 test_mcp.py

# Watch orchestrator logs
docker compose logs -f mcp-client-python
```
