from __future__ import annotations

import json

from mcp_types import CallToolResult, Tool


def to_anthropic_tools(mcp_tools: list[Tool]) -> list[dict]:
    """Convert whatever tools the MCP server currently advertises into Anthropic's tool-dict shape."""
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in mcp_tools
    ]


def unwrap_list_result(structured_content: object) -> list:
    """Unwrap a tool's structured_content into a plain list.

    MCP restricts structured_content to a JSON object at the root, so a tool
    returning a bare list (e.g. fetch_unread_emails) comes back wrapped as
    {"result": [...]} rather than the list itself.
    """
    if isinstance(structured_content, list):
        return structured_content
    if isinstance(structured_content, dict):
        result = structured_content.get("result")
        if isinstance(result, list):
            return result
    return []


def call_tool_result_to_content(result: CallToolResult) -> str:
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    return "\n".join(texts) or "(empty result)"
