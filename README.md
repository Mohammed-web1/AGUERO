# Email Intelligence Platform (AGUERO)

An advanced email intelligence platform designed to detect and flag threats (phishing, malware, spam) across major providers (Gmail, Outlook, Yahoo). Built using the Model Context Protocol (MCP).

## Core Capabilities
- **Threat Detection:** Classifies phishing, malware, and spam.
- **Priority & Urgency Triage:** Tags emails by importance (Critical, Normal, Low).
- **Promotion Filtering:** Allows users to toggle visibility of ads and newsletters.

## System Architecture

The project is built on a modular, multi-container architecture using Docker Compose:

```mermaid
graph TD
    A[mcp-client-python\nOrchestrator] --> B[mcp-server-rust\nConnector]
    A --> C[ai-service-fastapi\nIntelligence]
    C --> E[Ollama API\nexternal]
    B <--> D[(Email Providers\nGmail/Outlook)]
```

### 1. `mcp-client-python/` (The Orchestrator)
The brain of the operation. A Python-based MCP client that continuously monitors email accounts, sends content to the AI service for analysis, and commands the Rust server to apply labels or move messages.

### 2. `mcp-server-rust/` (The Connector)
A high-performance Rust MCP server. It connects securely to IMAP/OAuth endpoints of email providers and exposes safe tools (e.g., `fetch_emails`, `move_email`) to the orchestrator.

### 3. `ai-service-fastapi/` (The Intelligence Engine)
A Python FastAPI microservice exposing `POST /analyze`, which classifies one
email by threat level, urgency and category. The service holds all the logic --
prompting, validation, normalisation, and a keyword fallback so a slow or
failing model degrades triage instead of stopping it. Ollama is called as an
external API for the classification itself; nothing runs it here. See
[ai-service-fastapi/README.md](ai-service-fastapi/README.md).

## Getting Started

To spin up the entire system locally:
```bash
export ANTHROPIC_API_KEY=...   # used by the orchestrator
export OLLAMA_API_KEY=...      # used by the AI service (free: ollama.com)
docker compose up --build
```
