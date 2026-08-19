# AI Intelligence Engine

FastAPI microservice that classifies a single email on three independent axes.
It is the only component in AGUERO that talks to a classification model, so the
orchestrator stays free of ML concerns.

The classification itself is the only thing delegated: **Ollama is used as an
external API** (free tier at https://ollama.com), not as something this
platform runs. All the service logic — the contract, prompting, validation,
normalisation, fallback, error handling — lives here in FastAPI.

## Endpoints

### `POST /analyze`

The endpoint `mcp-client-python` calls once per unread email.

```jsonc
// request
{ "content": "From: ...\nSubject: ...\n\n<body>" }

// 200 response
{
  "threat_level": "Phishing",   // Safe | Phishing | Spam
  "urgency": "Critical",        // Critical | Normal | Low
  "category": "Update",         // Promotion | Update | Work
  "reason": "Lookalike sender domain demanding password confirmation.",
  "source": "ollama"            // ollama | heuristic
}
```

The first three fields are the contract `mcp_client/schemas.py` validates
against. `reason` and `source` are additive — pydantic ignores unknown fields,
so the orchestrator is unaffected, while logs show *why* a verdict was reached
and whether the model or the fallback produced it.

Errors: `422` on empty content, `503` only when the model fails **and**
`ENABLE_HEURISTIC_FALLBACK=false`.

### `GET /health`

Reports whether Ollama is reachable and whether the configured model is actually
pulled. Returns `200 status: "degraded"` when the model is missing but the
fallback can still answer — the service is degraded, not dead — and
`status: "unhealthy"` only when nothing can serve a verdict.

## Design notes

**The 10 second budget.** `mcp-client-python` gives up on `/analyze` after 10s
([`ai_service.py`](../mcp-client-python/src/mcp_client/ai_service.py)). The
upstream call therefore defaults to an 8s timeout, leaving room to answer from
the fallback instead of letting the orchestrator's poll cycle fail. Measured
against `llama3.2` (3B) on CPU, a steady-state classification takes ~5.3s.

**Heuristic fallback.** When Ollama is unreachable, too slow, or answers
off-contract, [`heuristics.py`](app/heuristics.py) answers from keyword patterns
instead. It is deliberately crude — its job is to keep triage moving with a
defensible verdict, not to compete with the model. Every such result is tagged
`"source": "heuristic"`, so a degraded pipeline is visible rather than silent.
Set `ENABLE_HEURISTIC_FALLBACK=false` to fail loudly instead.

**Prompt injection.** Email bodies are hostile input. The system prompt states
that instructions found inside an email are never to be followed and are
themselves a phishing signal, the body is delimited in an `<email>` tag, and
constrained decoding means a manipulated model still cannot emit anything
outside the three enums.

**Determinism.** `temperature: 0`, `top_p: 1` — the same email gets the same
verdict, which matters when the orchestrator acts on the result.

**Model choice and accuracy.** Spot-checked against eight hand-written emails
(credential phishing, CEO gift-card fraud, prompt injection, urgent colleague
mail, marketing, newsletter, receipt, lottery spam):

| Model | Correct | Latency | Notes |
| --- | --- | --- | --- |
| `gpt-oss:20b` (hosted, `think=low`) | 8/8 | 1.4–3.6s | No fallbacks triggered |
| `llama3.2` 3B (local, CPU) | 7/8 | ~5.3s | Over-escalates lottery spam to `Phishing` |

The local 3B model is what makes the no-key path practical and its one error is
conservative — the orchestrator quarantines `Phishing` and `Spam` alike — but the
hosted model is both more accurate and faster. `OLLAMA_MODEL` is the only change
needed. Note the hosted free tier only serves some models (`gpt-oss:20b`,
`gpt-oss:120b`, `gemma4:31b`); the rest return `403 requires a subscription`.

## Configuration

Copy `.env.example` to `.env`. Every value is also settable through the
environment (docker-compose passes them through).

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_API_KEY` | *(unset)* | **Required.** https://ollama.com/settings/keys |
| `OLLAMA_BASE_URL` | `https://ollama.com` | Or a self-hosted daemon's URL |
| `OLLAMA_MODEL` | `gpt-oss:20b` | Any model the endpoint serves |
| `OLLAMA_FORMAT_MODE` | `json` | `schema` only if the endpoint honours it |
| `OLLAMA_THINK` | `low` | `low`/`medium`/`high`/`false`; clear for non-reasoning models |
| `OLLAMA_TIMEOUT_SECONDS` | `9` | Must stay under the orchestrator's 10s timeout |
| `OLLAMA_NUM_CTX` | `4096` | Context window |
| `MAX_CONTENT_CHARS` | `6000` | Email text is truncated to this before analysis |
| `ENABLE_HEURISTIC_FALLBACK` | `true` | `false` → return 503 instead of falling back |
| `LOG_LEVEL` | `INFO` | |

## Running

### With Docker Compose (whole platform)

```bash
export ANTHROPIC_API_KEY=...       # orchestrator
export OLLAMA_API_KEY=...          # this service
docker compose up --build
```

Nothing else to provision: there is no model server to run, no weights to pull
and no volume to keep.

### Standalone

```bash
pip install -r requirements.txt
cp .env.example .env               # then put your key in it
uvicorn app.main:app --reload
```

Smoke test:

```bash
curl -s localhost:8000/health
curl -s localhost:8000/analyze -H 'content-type: application/json'   -d '{"content":"From: security@paypa1-alerts.com
Subject: Verify your account

Your account will be suspended. Click here to confirm your password."}'
```

## Tests

No network and no model needed — Ollama's HTTP API is mocked with `respx`.

```bash
pip install -r requirements-dev.txt
pytest -q
```

`pyproject.toml` supplies `pythonpath` and `testpaths`, so `pytest` finds the
`app` package with no per-test path juggling.

Covers the happy path, request shape and timeout sent upstream, truncation,
bearer-token auth, both format modes, the `think` field, synonym normalisation,
all four fallback paths (HTTP error, non-JSON, off-contract enum, empty),
timeout, the fallback-disabled 503, that the schema asks for every contract
field, and that the prompt keeps untrusted email inside its delimiter.

Health gets its own set: reachable and model present, model missing, Ollama
down, and — the case that used to return 500 — reachable but answering with
something unusable (an HTML error page, an empty body, a payload with no
`models` list). A health check must report degradation, never become the
outage, so every one of those asserts a 200 with `status: "degraded"`.

## Layout

| File | Role |
| --- | --- |
| [`app/main.py`](app/main.py) | FastAPI app, routes, fallback decision |
| [`app/schemas.py`](app/schemas.py) | Request/response contract and enums |
| [`app/config.py`](app/config.py) | Env-driven settings |
| [`app/ollama_client.py`](app/ollama_client.py) | `/api/chat` with structured output |
| [`app/prompts.py`](app/prompts.py) | System prompt, taxonomy, derived JSON schema |
| [`app/normalize.py`](app/normalize.py) | Coerces synonym values onto the contract |
| [`app/heuristics.py`](app/heuristics.py) | Keyword fallback classifier |
| [`requirements.in`](requirements.in) | Runtime dependencies, hand-edited ranges |
| [`requirements.txt`](requirements.txt) | Compiled pins — all the image installs |
| [`requirements-dev.in`](requirements-dev.in) | Test-only additions, hand-edited |
| [`requirements-dev.txt`](requirements-dev.txt) | Compiled pins for the test environment |
| [`pyproject.toml`](pyproject.toml) | Packaging metadata, pytest and ruff config |

## Dependencies

Runtime and test dependencies are kept apart deliberately: the Dockerfile
installs `requirements.txt` alone, so `pytest` and `respx` never reach the
production image. Anything needed only to run the tests belongs in
`requirements-dev.in`.

The `.in` files hold hand-edited ranges; the `.txt` files are fully pinned,
including transitive dependencies, so an image built today and one built in six
months install byte-identical package sets. Edit the `.in` file, then recompile:

```bash
uv pip compile requirements.in     -o requirements.txt     --python-version 3.11
uv pip compile requirements-dev.in -o requirements-dev.txt --python-version 3.11
```

`--python-version 3.11` matches the `python:3.11-slim` base image — resolving
against a different interpreter can select packages that image cannot install.
`pyproject.toml` reads the ranges from `requirements.in`, since package metadata
should say what the service is compatible with rather than what one image pinned.

The contract's values live in exactly one place — the enums in `app/schemas.py`.
The JSON schema sent to Ollama and the normaliser's lookup tables are both
derived from them, so there is nothing to keep manually in sync. The
orchestrator restates them in its own `schemas.py`, which is deliberate: the two
services are separate deployables that validate the boundary independently.
