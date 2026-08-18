from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    ai_service_url: str
    mcp_server_url: str
    poll_interval_seconds: int
    claude_model: str


def load_settings() -> Settings:
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required. Set it in mcp-client-python/.env or the environment."
        )

    return Settings(
        anthropic_api_key=api_key,
        ai_service_url=os.environ.get("AI_SERVICE_URL", "http://localhost:8000"),
        mcp_server_url=os.environ.get("MCP_SERVER_URL", "http://localhost:8080/mcp"),
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
    )
