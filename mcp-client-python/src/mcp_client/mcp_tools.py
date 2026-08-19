from __future__ import annotations

import json
from typing import Any

from mcp_types import CallToolResult, Tool


def to_anthropic_tools(mcp_tools: list[Tool]) -> list[dict]:
    """Convert whatever tools the MCP server currently advertises into Anthropic's tool-dict shape."""
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in mcp_tools
    ]


# Keys a fetch tool might return its list under, in preference order. "result"
# is the spec-conformant wrapper; "unread_email_ids" is what mcp-server-rust
# returns today. Checked in order so a server sending both wins on the former.
_LIST_KEYS = ("result", "emails", "unread_email_ids")


def _as_email(item: Any) -> dict[str, Any]:
    """Coerce one list element into an email-shaped mapping.

    A conformant server returns objects with sender/subject/snippet. The Rust
    server currently returns bare IMAP ids, which carry no content at all --
    wrapping them keeps the rest of the pipeline uniform, and the analysis such
    an email gets is necessarily weak rather than crashing the poll cycle.
    """
    if isinstance(item, dict):
        return item
    return {"id": str(item)}


def unwrap_list_result(structured_content: object) -> list[dict[str, Any]]:
    """Unwrap a tool's structured_content into a plain list of email dicts.

    Three shapes are accepted, because the servers in play disagree:

    - a bare list, from a server that returns one directly;
    - ``{"result": [...]}`` -- MCP restricts structured_content to a JSON object
      at the root, so a tool returning a bare list (e.g. fetch_unread_emails)
      comes back wrapped rather than as the list itself;
    - ``{"unread_email_ids": [...]}`` -- mcp-server-rust's current response.

    Anything else yields an empty list.
    """
    raw: list[Any] | None = None

    if isinstance(structured_content, list):
        raw = structured_content
    elif isinstance(structured_content, dict):
        for key in _LIST_KEYS:
            value = structured_content.get(key)
            if isinstance(value, list):
                raw = value
                break

    if raw is None:
        return []
    return [_as_email(item) for item in raw]


def call_tool_result_to_content(result: CallToolResult) -> str:
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    return "\n".join(texts) or "(empty result)"
