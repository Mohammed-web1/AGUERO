"""Shared fixtures and MCP test doubles for the orchestrator's tests.

Nothing here touches the network: the AI service and Ollama are mocked with
`respx`, and the MCP server is replaced by `FakeMcpSession`, which records the
tool calls the orchestrator makes so they can be asserted on.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# Pin configuration before anything imports it. `load_dotenv` does not override
# variables that are already set, so this also stops a developer's real .env
# (or a real ANTHROPIC_API_KEY in the environment) from reaching these tests.
os.environ.update(
    {
        "LLM_PROVIDER": "ollama",
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_MODEL": "test-claude",
        "OLLAMA_BASE_URL": "http://ollama.test:11434",
        "OLLAMA_API_KEY": "test-key",
        "OLLAMA_MODEL": "test-model",
        "OLLAMA_FORMAT_MODE": "json",
        "OLLAMA_THINK": "low",
        "OLLAMA_TIMEOUT_SECONDS": "30",
        "AI_SERVICE_URL": "http://ai.test:8000",
        "MCP_SERVER_URL": "http://mcp.test:8080/mcp",
        "MCP_TRANSPORT": "auto",
        "POLL_INTERVAL_SECONDS": "60",
    }
)

from mcp_client.config import load_settings  # noqa: E402


class FakeTool:
    """Minimal stand-in for mcp_types.Tool."""

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema


class FakeListToolsResult:
    def __init__(self, tools: list[FakeTool]) -> None:
        self.tools = tools


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeToolResult:
    """Minimal stand-in for mcp_types.CallToolResult."""

    def __init__(
        self,
        structured_content: object = None,
        content: list[Any] | None = None,
        is_error: bool = False,
    ) -> None:
        self.structured_content = structured_content
        self.content = content or []
        self.is_error = is_error


DEFAULT_TOOLS = [
    FakeTool(
        "fetch_unread_emails",
        "Fetches a list of unread emails from the inbox.",
        {"type": "object", "properties": {}},
    ),
    FakeTool(
        "apply_label",
        "Applies a classification label to an email.",
        {
            "type": "object",
            "properties": {"email_id": {"type": "string"}, "label": {"type": "string"}},
            "required": ["email_id", "label"],
        },
    ),
    FakeTool(
        "move_email",
        "Moves an email to a specific folder.",
        {
            "type": "object",
            "properties": {"email_id": {"type": "string"}, "folder": {"type": "string"}},
            "required": ["email_id", "folder"],
        },
    ),
]


class FakeMcpSession:
    """Records every tool call, and answers them from a scripted table."""

    def __init__(
        self,
        tools: list[FakeTool] | None = None,
        fetch_result: object = None,
        tool_errors: set[str] | None = None,
    ) -> None:
        self._tools = DEFAULT_TOOLS if tools is None else tools
        self._fetch_result = fetch_result
        self._tool_errors = tool_errors or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, *args: Any, **kwargs: Any) -> FakeListToolsResult:
        return FakeListToolsResult(self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> FakeToolResult:
        self.calls.append((name, arguments or {}))
        if name in self._tool_errors:
            return FakeToolResult(
                content=[FakeTextBlock("boom")], structured_content=None, is_error=True
            )
        if name == "fetch_unread_emails":
            return FakeToolResult(structured_content=self._fetch_result)
        return FakeToolResult(structured_content={"success": True})

    def calls_to(self, name: str) -> list[dict[str, Any]]:
        return [args for called, args in self.calls if called == name]


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def mcp_session():
    return FakeMcpSession(
        fetch_result={
            "result": [
                {
                    "id": "1",
                    "sender": "security@paypa1-verify.com",
                    "subject": "Urgent: Verify your account now",
                    "snippet": "Your account will be suspended unless you verify.",
                }
            ]
        }
    )


@pytest.fixture
def patch_mcp_session(monkeypatch):
    """Swap the real transport for a FakeMcpSession in `run_once`."""
    from contextlib import asynccontextmanager

    import mcp_client.client as client_module

    def _install(session: FakeMcpSession) -> FakeMcpSession:
        @asynccontextmanager
        async def fake_open(_settings):
            yield session

        monkeypatch.setattr(client_module, "open_mcp_session", fake_open)
        return session

    return _install
