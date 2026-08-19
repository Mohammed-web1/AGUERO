from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_provider: str  # "ollama"
    anthropic_api_key: str | None
    claude_model: str
    ollama_base_url: str
    ollama_api_key: str | None
    ollama_model: str
    ollama_timeout_seconds: float
    ollama_format_mode: str  # "schema" or "json"
    ollama_think: str | None
    ai_service_url: str
    mcp_server_url: str

    # Which MCP transport to open. "auto" lets the SDK negotiate, which means
    # Streamable HTTP (POST to the server URL). "sse" forces the older HTTP+SSE
    # transport -- GET the server URL for the event stream, POST commands to the
    # endpoint it advertises -- which is what mcp-server-rust implements.
    mcp_transport: str  # "auto" or "sse"

    poll_interval_seconds: int

    @property
    def think(self) -> str | bool | None:
        """`ollama_think` as Ollama's API wants it: a level, a bool, or absent."""
        raw = (self.ollama_think or "").strip().lower()
        if not raw or raw == "none":
            return None
        if raw in {"true", "false"}:
            return raw == "true"
        return raw


def load_settings() -> Settings:
    load_dotenv()

    llm_provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if llm_provider not in {"anthropic", "ollama"}:
        raise RuntimeError(f"LLM_PROVIDER must be 'anthropic' or 'ollama', got {llm_provider!r}")

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if llm_provider == "anthropic" and not anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic. "
            "Set it in mcp-client-python/.env or the environment."
        )

    mcp_transport = os.environ.get("MCP_TRANSPORT", "auto").strip().lower()
    if mcp_transport not in {"auto", "sse"}:
        raise RuntimeError(f"MCP_TRANSPORT must be 'auto' or 'sse', got {mcp_transport!r}")

    return Settings(
        llm_provider=llm_provider,
        anthropic_api_key=anthropic_api_key,
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "https://ollama.com").rstrip("/"),
        ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "gpt-oss:20b"),
        ollama_timeout_seconds=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "30")),
        ollama_format_mode=os.environ.get("OLLAMA_FORMAT_MODE", "json"),
        ollama_think=os.environ.get("OLLAMA_THINK", "low"),
        ai_service_url=os.environ.get("AI_SERVICE_URL", "http://localhost:8000"),
        mcp_server_url=os.environ.get("MCP_SERVER_URL", "http://localhost:8080/mcp"),
        mcp_transport=mcp_transport,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
    )
