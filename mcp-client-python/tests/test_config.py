"""Settings parsing and the validation that fails fast at startup."""

from __future__ import annotations

import pytest

from mcp_client.config import load_settings


def test_defaults_come_from_the_environment(settings):
    assert settings.llm_provider == "ollama"
    assert settings.ai_service_url == "http://ai.test:8000"
    assert settings.mcp_server_url == "http://mcp.test:8080/mcp"
    assert settings.mcp_transport == "auto"
    assert settings.poll_interval_seconds == 60


def test_trailing_slash_is_stripped_from_the_ollama_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/")
    assert load_settings().ollama_base_url == "https://ollama.com"


def test_unknown_llm_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt5")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        load_settings()


def test_anthropic_without_a_key_is_rejected(monkeypatch):
    """Better to fail at startup than once an email is already in flight."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        load_settings()


def test_anthropic_with_a_key_is_accepted(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert load_settings().llm_provider == "anthropic"


@pytest.mark.parametrize("transport", ["auto", "sse", "SSE", " auto "])
def test_valid_transports_are_accepted(monkeypatch, transport):
    monkeypatch.setenv("MCP_TRANSPORT", transport)
    assert load_settings().mcp_transport == transport.strip().lower()


def test_unknown_transport_is_rejected(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "websocket")
    with pytest.raises(RuntimeError, match="MCP_TRANSPORT"):
        load_settings()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("low", "low"), ("high", "high"), ("true", True), ("false", False), ("", None), ("none", None)],
)
def test_think_is_shaped_as_the_ollama_api_wants(monkeypatch, configured, expected):
    monkeypatch.setenv("OLLAMA_THINK", configured)
    think = load_settings().think
    # Type matters as much as value here: Ollama treats "false" and False
    # differently, so a bool must not arrive as the string "false".
    assert think == expected
    assert type(think) is type(expected)
