# AGUERO — Testing Guide

This document explains how to set up, run, and test the AGUERO Email Intelligence Platform.

---

## Prerequisites

Before you begin, make sure you have installed:

| Tool | Version | Purpose |
|---|---|---|
| **Docker** | 20+ | Runs all 4 services in containers |
| **Docker Compose** | v2+ | Orchestrates the multi-container setup |
| **Git** | Any | Clone the repository |
| **A web browser** | Any modern browser | Use the dashboard UI |

**Optional** (only if you want to connect a real Gmail account):
- A Gmail account with [2-Step Verification](https://myaccount.google.com/security) enabled
- A [Gmail App Password](https://myaccount.google.com/apppasswords) (16 characters, no spaces)

---

## Step 1 — Clone the repository

```bash
git clone https://github.com/YOUR_ORG/AGUERO.git
cd AGUERO
```

---

## Step 2 — Configure environment variables

Copy the example configuration and fill in your API keys:

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in:

```env
# REQUIRED — Get a free key at https://ollama.com/settings/keys
OLLAMA_API_KEY=your_ollama_api_key_here

# OPTIONAL — Only if you want the orchestrator to use Anthropic Claude
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# OPTIONAL — Only if you want to connect a real Gmail account
IMAP_USER=your.email@gmail.com
IMAP_PASS=your_gmail_app_password
```

> 🔒 **Important:** Never push the `.env` file to GitHub. It contains secrets.

---

## Step 3 — Start all services

```bash
docker compose up -d --build
```

This starts 4 containers:

| Container | Port | Role |
|---|---|---|
| `mcp-server-rust` | `8080` | MCP protocol server (IMAP + user management) |
| `ai-service-fastapi` | `8000` | AI classification engine (LLM + heuristic fallback) |
| `mcp-client-python` | — | Orchestrator (polls inbox every 60 seconds) |
| `postgres` | `5432` | PostgreSQL database (users + classification history) |

---

## Step 4 — Verify services are running

```bash
docker compose ps
```

All containers should show `Up` or `healthy`.

Check the AI engine health:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "model": "gpt-oss:20b",
  "ollama_reachable": true,
  "model_available": true
}
```

Check the MCP server is running:

```bash
curl http://localhost:8080/users
```

Expected response (empty list if no users registered yet):
```json
[]
```

---

## Step 5 — Open the Dashboard

Open the file `dashboard/index.html` in your browser:

```
file:///path/to/AGUERO/dashboard/index.html
```

The dashboard has 4 tabs:

| Tab | What it does |
|---|---|
| **Dashboard** | Shows live stats (Phishing / Spam / Safe counts) and recent activity |
| **Analyze Email** | Paste any email → get instant AI classification |
| **Users** | Register Gmail accounts for automatic monitoring |
| **System Health** | Live status of all 4 services |

The sidebar at the bottom-left shows green dots if both services are reachable.

---

## Step 6 — Test the AI Classifier

### Option A: Use the Dashboard UI

1. Click **Analyze Email** in the sidebar
2. Click one of the example buttons: **🎣 Phishing**, **📢 Spam**, **📋 Work Email**, or **🛍️ Promotion**
3. Click **🔍 Analyze Email**
4. The AI verdict appears on the right (threat level, urgency, category, and reason)

### Option B: Use curl

**Test a phishing email:**
```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"content": "From: security@paypa1-alerts.com\nSubject: Urgent: verify your account\n\nClick here to confirm your password or your account will be suspended."}'
```

Expected:
```json
{
  "threat_level": "Phishing",
  "urgency": "Critical",
  "category": "Update",
  "reason": "...",
  "source": "ollama"
}
```

**Test a safe work email:**
```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"content": "From: jane@company.com\nSubject: Sprint review Friday\n\nHi team, let us meet at 3pm to review the sprint."}'
```

Expected:
```json
{
  "threat_level": "Safe",
  "urgency": "Normal",
  "category": "Work",
  "reason": "...",
  "source": "ollama"
}
```

---

## Step 7 — Test the Live Simulation

1. Go to the **Dashboard** tab
2. Click the **▶ Simulate Live Traffic** button (top-right)
3. The frontend automatically generates random emails every 6 seconds and sends them to the backend API
4. Watch the stats (Phishing Detected / Spam Blocked / Safe Emails) increase in real time
5. Click **⏹ Stop Simulation** to stop

> **Note:** The dashboard is a visualization tool. The frontend generates the mock emails. The backend API (`ai-service-fastapi`) performs the actual AI classification. This proves the API works correctly in a live scenario.

---

## Step 8 — Test User Registration

### Option A: Use the Dashboard UI

1. Click **Users** in the sidebar
2. Fill in:
   - **Choose a Username:** any name (e.g. `demo_user`)
   - **Gmail Address:** your Gmail address
   - **Gmail App Password:** your 16-character app password
3. Click **Connect**
4. The user appears with a green **🛡️ Protected** badge

### Option B: Use curl

```bash
curl -X POST http://localhost:8080/register \
  -H "Content-Type: application/json" \
  -d '{"user_id": "demo_user", "email": "demo@gmail.com", "app_password": "xxxx-yyyy-zzzz-aaaa"}'
```

Expected:
```json
{
  "status": "success",
  "message": "User 'demo_user' successfully registered and encrypted in PostgreSQL",
  "user_id": "demo_user"
}
```

Verify the user was saved:
```bash
curl http://localhost:8080/users
```

---

## Step 9 — Check logs

Watch all services in real time:

```bash
docker compose logs -f
```

Or check individual services:

```bash
# AI engine logs
docker compose logs -f ai-service-fastapi

# Rust MCP server logs
docker compose logs -f mcp-server-rust

# Orchestrator logs (shows inbox polling)
docker compose logs -f mcp-client-python
```

---

## Step 10 — Stop everything

```bash
docker compose down
```

To also delete the database data:

```bash
docker compose down -v
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| AI Engine shows ✗ in dashboard | Check `OLLAMA_API_KEY` in `.env`, then `docker compose restart ai-service-fastapi` |
| MCP Server shows ✗ | Run `docker compose logs mcp-server-rust` to see errors |
| `curl /health` returns connection refused | Services are still starting — wait 30 seconds and retry |
| Registration fails | Make sure `postgres` container is healthy: `docker compose ps` |
| Orchestrator not polling | Check that `ANTHROPIC_API_KEY` or Ollama keys are set in `.env` |

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────┐
│                   AGUERO Platform                    │
│                                                     │
│  ┌──────────────┐     ┌───────────────────────┐     │
│  │  Dashboard    │────▶│  ai-service-fastapi   │     │
│  │  (Frontend)   │     │  POST /analyze        │     │
│  │              │     │  GET  /health          │     │
│  └──────────────┘     └───────────────────────┘     │
│         │                        ▲                   │
│         │                        │                   │
│         ▼                        │                   │
│  ┌──────────────┐     ┌───────────────────────┐     │
│  │ mcp-server   │     │  mcp-client-python    │     │
│  │ (Rust)       │◀────│  (Orchestrator)       │     │
│  │ POST /register     │  Polls every 60s      │     │
│  │ GET  /users  │     │  Classifies + labels  │     │
│  └──────┬───────┘     └───────────────────────┘     │
│         │                                            │
│         ▼                                            │
│  ┌──────────────┐                                    │
│  │  PostgreSQL   │                                   │
│  │  Users + Logs │                                   │
│  └──────────────┘                                    │
└─────────────────────────────────────────────────────┘
```
