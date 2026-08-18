from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.prompts import build_response_schema
from app.schemas import AnalysisResult

CHAT_URL = "http://ollama.test:11434/api/chat"
TAGS_URL = "http://ollama.test:11434/api/tags"

PHISHING_EMAIL = (
    "From: security@paypa1-alerts.com\n"
    "Subject: Urgent: verify your account within 24 hours\n\n"
    "Unusual sign-in activity detected. Click here to confirm your password "
    "or your account will be suspended."
)


def _chat_response(**overrides: object) -> httpx.Response:
    verdict = {
        "threat_level": "Phishing",
        "urgency": "Critical",
        "category": "Update",
        "reason": "Lookalike sender domain demanding password confirmation.",
    }
    verdict.update(overrides)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(verdict)}})


@respx.mock
def test_analyze_returns_model_verdict(client):
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    response = client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert response.status_code == 200
    assert response.json() == {
        "threat_level": "Phishing",
        "urgency": "Critical",
        "category": "Update",
        "reason": "Lookalike sender domain demanding password confirmation.",
        "source": "ollama",
    }

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test-model"
    assert sent["stream"] is False
    assert sent["format"] == build_response_schema()
    assert sent["options"]["temperature"] == 0
    assert PHISHING_EMAIL in sent["messages"][1]["content"]
    # A real request stays inside the orchestrator's 10s budget.
    assert route.calls.last.request.extensions["timeout"]["read"] == 9.0


@respx.mock
def test_long_content_is_truncated_before_reaching_ollama(client):
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    client.post("/analyze", json={"content": "x" * 20_000})

    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][1]["content"].count("x") == 6000


@respx.mock
@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="internal error"),
        httpx.Response(200, json={"message": {"content": "not json at all"}}),
        httpx.Response(200, json={"message": {"content": json.dumps({"threat_level": "Nuclear"})}}),
        httpx.Response(200, json={"message": {"content": ""}}),
    ],
    ids=["http-500", "non-json", "off-contract-enum", "empty"],
)
def test_falls_back_to_heuristics_when_ollama_misbehaves(client, failure):
    respx.post(CHAT_URL).mock(return_value=failure)

    response = client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "heuristic"
    assert body["threat_level"] == "Phishing"
    # Still valid against the contract the orchestrator validates.
    AnalysisResult.model_validate(body)


@respx.mock
def test_falls_back_on_timeout(client):
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    response = client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert response.status_code == 200
    assert response.json()["source"] == "heuristic"


@respx.mock
def test_returns_503_when_fallback_disabled(client, monkeypatch):
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_HEURISTIC_FALLBACK", "false")
    get_settings.cache_clear()
    assert Settings().enable_heuristic_fallback is False

    respx.post(CHAT_URL).mock(return_value=httpx.Response(502, text="bad gateway"))

    response = client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert response.status_code == 503


def test_empty_content_is_rejected(client):
    assert client.post("/analyze", json={"content": "   "}).status_code == 422
    assert client.post("/analyze", json={}).status_code == 422


@respx.mock
def test_api_key_is_sent_as_bearer_token(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("OLLAMA_API_KEY", "secret-key")
    get_settings.cache_clear()

    # The client is built at startup, so rebuild it against refreshed settings.
    from app.main import app as fastapi_app
    from app.ollama_client import OllamaClient

    fastapi_app.state.ollama = OllamaClient(get_settings(), fastapi_app.state.http_client)

    route = respx.post(CHAT_URL).mock(return_value=_chat_response())
    client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert route.calls.last.request.headers["authorization"] == "Bearer secret-key"


@respx.mock
def test_health_reports_ok_when_model_present(client):
    respx.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
    )

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["ollama_reachable"] is True
    assert body["model_available"] is True


@respx.mock
def test_health_reports_degraded_when_model_missing(client):
    respx.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "some-other-model:latest"}]})
    )

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["model_available"] is False
    assert "not pulled" in body["detail"]


@respx.mock
def test_health_reports_degraded_when_ollama_is_down(client):
    respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["ollama_reachable"] is False


@respx.mock
def test_json_format_mode_sends_plain_json_not_schema(client, monkeypatch):
    """The hosted API ignores schema-constrained decoding, so we ask for JSON mode."""
    from app.config import get_settings
    from app.main import app as fastapi_app
    from app.ollama_client import OllamaClient

    monkeypatch.setenv("OLLAMA_FORMAT_MODE", "json")
    get_settings.cache_clear()
    fastapi_app.state.ollama = OllamaClient(get_settings(), fastapi_app.state.http_client)

    route = respx.post(CHAT_URL).mock(return_value=_chat_response())
    assert client.post("/analyze", json={"content": PHISHING_EMAIL}).status_code == 200

    assert json.loads(route.calls.last.request.content)["format"] == "json"


@respx.mock
def test_synonym_values_are_normalised_onto_the_contract(client, monkeypatch):
    """JSON mode does not constrain enums, so near-miss values are coerced."""
    from app.config import get_settings
    from app.main import app as fastapi_app
    from app.ollama_client import OllamaClient

    monkeypatch.setenv("OLLAMA_FORMAT_MODE", "json")
    get_settings.cache_clear()
    fastapi_app.state.ollama = OllamaClient(get_settings(), fastapi_app.state.http_client)

    respx.post(CHAT_URL).mock(
        return_value=_chat_response(threat_level="malicious", urgency="high", category="marketing")
    )

    body = client.post("/analyze", json={"content": PHISHING_EMAIL}).json()

    assert body["source"] == "ollama"
    assert (body["threat_level"], body["urgency"], body["category"]) == (
        "Phishing",
        "Critical",
        "Promotion",
    )


@respx.mock
def test_think_is_omitted_by_default(client):
    """Non-reasoning models reject the field with a 400, so never send it blind."""
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())
    client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert "think" not in json.loads(route.calls.last.request.content)


@respx.mock
@pytest.mark.parametrize(
    ("configured", "expected"),
    [("low", "low"), ("high", "high"), ("false", False), ("true", True)],
)
def test_think_is_sent_when_configured(client, monkeypatch, configured, expected):
    from app.config import get_settings
    from app.main import app as fastapi_app
    from app.ollama_client import OllamaClient

    monkeypatch.setenv("OLLAMA_THINK", configured)
    get_settings.cache_clear()
    fastapi_app.state.ollama = OllamaClient(get_settings(), fastapi_app.state.http_client)

    route = respx.post(CHAT_URL).mock(return_value=_chat_response())
    client.post("/analyze", json={"content": PHISHING_EMAIL})

    assert json.loads(route.calls.last.request.content)["think"] == expected
