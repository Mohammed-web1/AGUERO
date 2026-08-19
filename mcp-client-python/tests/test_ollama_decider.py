"""The Ollama decision path: prompt shape in, validated actions out."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from mcp_client.mcp_tools import to_anthropic_tools
from mcp_client.ollama_decider import OllamaDecider, OllamaError

from .conftest import DEFAULT_TOOLS

CHAT_URL = "http://ollama.test:11434/api/chat"

EMAIL = {"id": "1", "sender": "a@b.test", "subject": "Verify your account", "snippet": "..."}
ANALYSIS = {"threat_level": "Phishing", "urgency": "Critical", "category": "Update"}

DECISION = {
    "actions": [
        {"tool": "apply_label", "arguments": {"email_id": "1", "label": "Phishing"}},
        {"tool": "move_email", "arguments": {"email_id": "1", "folder": "Quarantine"}},
    ],
    "reason": "Lookalike domain demanding credentials.",
}


def _chat_response(decision: dict | str = DECISION) -> httpx.Response:
    raw = decision if isinstance(decision, str) else json.dumps(decision)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": raw}})


@pytest.fixture
def tools():
    return to_anthropic_tools(DEFAULT_TOOLS)


@respx.mock
async def test_actions_are_returned(settings, tools):
    respx.post(CHAT_URL).mock(return_value=_chat_response())

    async with httpx.AsyncClient() as http_client:
        decision = await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)

    assert [a["tool"] for a in decision["actions"]] == ["apply_label", "move_email"]


@respx.mock
async def test_request_is_deterministic_and_json_constrained(settings, tools):
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    async with httpx.AsyncClient() as http_client:
        await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test-model"
    assert sent["stream"] is False
    assert sent["format"] == "json"
    assert sent["options"] == {"temperature": 0, "top_p": 1}
    assert sent["think"] == "low"


@respx.mock
async def test_schema_mode_sends_the_full_schema(settings, tools, monkeypatch):
    from mcp_client.config import load_settings

    monkeypatch.setenv("OLLAMA_FORMAT_MODE", "schema")
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    async with httpx.AsyncClient() as http_client:
        await OllamaDecider(load_settings(), http_client).decide(tools, EMAIL, ANALYSIS)

    assert json.loads(route.calls.last.request.content)["format"]["type"] == "object"


@respx.mock
async def test_tools_email_and_analysis_all_reach_the_prompt(settings, tools):
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    async with httpx.AsyncClient() as http_client:
        await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)

    prompt = json.loads(route.calls.last.request.content)["messages"][1]["content"]
    assert "apply_label" in prompt and "move_email" in prompt
    assert "Verify your account" in prompt
    assert "Phishing" in prompt


@respx.mock
async def test_api_key_is_sent_as_a_bearer_token(settings, tools):
    route = respx.post(CHAT_URL).mock(return_value=_chat_response())

    async with httpx.AsyncClient() as http_client:
        await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)

    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
@pytest.mark.parametrize(
    ("failure", "match"),
    [
        (httpx.Response(500, text="boom"), "HTTP 500"),
        (_chat_response("not json at all"), "did not return JSON"),
        (_chat_response({"reason": "no actions key"}), "missing an 'actions' list"),
        (httpx.Response(200, json={"message": {"content": ""}}), "empty message"),
    ],
    ids=["http-500", "non-json", "missing-actions", "empty"],
)
async def test_unusable_answers_raise_ollama_error(settings, tools, failure, match):
    respx.post(CHAT_URL).mock(return_value=failure)

    async with httpx.AsyncClient() as http_client:
        with pytest.raises(OllamaError, match=match):
            await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)


@respx.mock
async def test_timeout_is_reported_as_ollama_error(settings, tools):
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    async with httpx.AsyncClient() as http_client:
        with pytest.raises(OllamaError, match="did not answer within"):
            await OllamaDecider(settings, http_client).decide(tools, EMAIL, ANALYSIS)
