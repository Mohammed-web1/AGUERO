"""The adapter layer between MCP responses and the rest of the orchestrator.

`unwrap_list_result` is the seam where the servers in play disagree, so the
shapes it must tolerate are pinned here rather than discovered in production.
"""

from __future__ import annotations

import json

import pytest

from mcp_client.mcp_tools import (
    call_tool_result_to_content,
    to_anthropic_tools,
    unwrap_list_result,
)

from .conftest import FakeTextBlock, FakeTool, FakeToolResult

EMAIL = {"id": "1", "sender": "a@b.test", "subject": "hi", "snippet": "..."}


def test_bare_list_passes_through():
    assert unwrap_list_result([EMAIL]) == [EMAIL]


def test_result_wrapper_is_unwrapped():
    """MCP restricts structured_content to an object, so lists arrive wrapped."""
    assert unwrap_list_result({"result": [EMAIL]}) == [EMAIL]


def test_rust_servers_unread_email_ids_shape_is_understood():
    """mcp-server-rust returns bare IMAP ids under its own key, not "result"."""
    assert unwrap_list_result({"status": "success", "unread_email_ids": [1, 2, 3]}) == [
        {"id": "1"},
        {"id": "2"},
        {"id": "3"},
    ]


def test_bare_ids_are_normalised_so_the_pipeline_stays_uniform():
    """Downstream code does email.get(...); scalars would raise AttributeError."""
    for email in unwrap_list_result({"unread_email_ids": [7]}):
        assert email.get("id") == "7"
        assert email.get("sender", "") == ""


def test_result_key_wins_over_the_rust_fallback():
    structured = {"result": [EMAIL], "unread_email_ids": [9]}
    assert unwrap_list_result(structured) == [EMAIL]


@pytest.mark.parametrize(
    "structured",
    [None, {}, {"status": "success"}, {"result": "not-a-list"}, 42, "text"],
    ids=["none", "empty", "no-list-key", "non-list-value", "int", "str"],
)
def test_unrecognised_shapes_yield_an_empty_list(structured):
    assert unwrap_list_result(structured) == []


def test_to_anthropic_tools_maps_the_sdk_field_names():
    tools = to_anthropic_tools(
        [FakeTool("apply_label", "Applies a label.", {"type": "object", "properties": {}})]
    )
    assert tools == [
        {
            "name": "apply_label",
            "description": "Applies a label.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_to_anthropic_tools_tolerates_a_missing_description():
    assert to_anthropic_tools([FakeTool("x", None, {})])[0]["description"] == ""


def test_content_prefers_structured_output():
    result = FakeToolResult(structured_content={"success": True})
    assert json.loads(call_tool_result_to_content(result)) == {"success": True}


def test_content_falls_back_to_text_blocks():
    result = FakeToolResult(content=[FakeTextBlock("first"), FakeTextBlock("second")])
    assert call_tool_result_to_content(result) == "first\nsecond"


def test_content_never_returns_an_empty_string():
    """An empty tool_result content block is rejected by the Anthropic API."""
    assert call_tool_result_to_content(FakeToolResult()) == "(empty result)"
