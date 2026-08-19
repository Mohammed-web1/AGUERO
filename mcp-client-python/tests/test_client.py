"""The poll cycle itself: fetch -> analyze -> act.

This is the loop that had no coverage, and the seam where the three services'
contracts meet. The MCP server is a `FakeMcpSession`; the AI service and Ollama
are mocked with `respx`.
"""

from __future__ import annotations

import json

import httpx
import respx

from mcp_client.client import _backoff_seconds, run_once
from mcp_client.config import load_settings

from .conftest import FakeMcpSession

ANALYZE_URL = "http://ai.test:8000/analyze"
CHAT_URL = "http://ollama.test:11434/api/chat"

PHISHING = {
    "id": "1",
    "sender": "security@paypa1-verify.com",
    "subject": "Urgent: Verify your account now",
    "snippet": "Your account will be suspended unless you verify.",
}
SAFE = {"id": "3", "sender": "jane@colleague.test", "subject": "Project sync", "snippet": "notes"}

PHISHING_VERDICT = {"threat_level": "Phishing", "urgency": "Critical", "category": "Update"}
SAFE_VERDICT = {"threat_level": "Safe", "urgency": "Normal", "category": "Work"}

QUARANTINE_DECISION = {
    "actions": [
        {"tool": "apply_label", "arguments": {"email_id": "1", "label": "Phishing"}},
        {"tool": "move_email", "arguments": {"email_id": "1", "folder": "Quarantine"}},
    ],
    "reason": "Credential phishing.",
}
NO_ACTION_DECISION = {"actions": [], "reason": "Routine colleague mail."}


def _chat(decision: dict) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": json.dumps(decision)}})


def _decider(settings, http_client):
    from mcp_client.ollama_decider import OllamaDecider

    return OllamaDecider(settings, http_client)


async def _run(settings, session, patch_mcp_session):
    patch_mcp_session(session)
    async with httpx.AsyncClient() as http_client:
        await run_once(settings, None, _decider(settings, http_client), http_client)


@respx.mock
async def test_phishing_email_is_labelled_and_quarantined(settings, patch_mcp_session):
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(return_value=_chat(QUARANTINE_DECISION))
    session = FakeMcpSession(fetch_result={"result": [PHISHING]})

    await _run(settings, session, patch_mcp_session)

    assert session.calls_to("apply_label") == [{"email_id": "1", "label": "Phishing"}]
    assert session.calls_to("move_email") == [{"email_id": "1", "folder": "Quarantine"}]


@respx.mock
async def test_safe_email_is_left_alone(settings, patch_mcp_session):
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=SAFE_VERDICT))
    respx.post(CHAT_URL).mock(return_value=_chat(NO_ACTION_DECISION))
    session = FakeMcpSession(fetch_result={"result": [SAFE]})

    await _run(settings, session, patch_mcp_session)

    assert session.calls_to("apply_label") == []
    assert session.calls_to("move_email") == []


@respx.mock
async def test_the_rust_servers_id_only_response_still_drives_a_cycle(settings, patch_mcp_session):
    """The regression that made the loop a silent no-op: ids under `unread_email_ids`."""
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(return_value=_chat(QUARANTINE_DECISION))
    session = FakeMcpSession(fetch_result={"status": "success", "unread_email_ids": [1]})

    await _run(settings, session, patch_mcp_session)

    assert session.calls_to("apply_label") == [{"email_id": "1", "label": "Phishing"}]


@respx.mock
async def test_an_empty_inbox_calls_no_action_tools(settings, patch_mcp_session):
    session = FakeMcpSession(fetch_result={"result": []})

    await _run(settings, session, patch_mcp_session)

    assert session.calls == [("fetch_unread_emails", {})]


@respx.mock
async def test_an_ai_service_failure_skips_only_that_email(settings, patch_mcp_session):
    respx.post(ANALYZE_URL).mock(
        side_effect=[
            httpx.Response(503, text="down"),
            httpx.Response(200, json=PHISHING_VERDICT),
        ]
    )
    respx.post(CHAT_URL).mock(return_value=_chat(QUARANTINE_DECISION))
    session = FakeMcpSession(fetch_result={"result": [SAFE, PHISHING]})

    await _run(settings, session, patch_mcp_session)

    # The first email was skipped; the second still went all the way through.
    assert session.calls_to("apply_label") == [{"email_id": "1", "label": "Phishing"}]


@respx.mock
async def test_an_ollama_failure_takes_no_action_on_the_mailbox(settings, patch_mcp_session):
    """A model that cannot decide must never fall through to acting anyway."""
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    session = FakeMcpSession(fetch_result={"result": [PHISHING]})

    await _run(settings, session, patch_mcp_session)

    assert session.calls == [("fetch_unread_emails", {})]


@respx.mock
async def test_a_hallucinated_tool_is_refused(settings, patch_mcp_session):
    """Only tools the server actually advertises may be called."""
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(
        return_value=_chat(
            {"actions": [{"tool": "delete_mailbox", "arguments": {}}], "reason": "nope"}
        )
    )
    session = FakeMcpSession(fetch_result={"result": [PHISHING]})

    await _run(settings, session, patch_mcp_session)

    assert session.calls_to("delete_mailbox") == []


@respx.mock
async def test_a_failing_tool_does_not_abort_the_remaining_actions(settings, patch_mcp_session):
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(return_value=_chat(QUARANTINE_DECISION))
    session = FakeMcpSession(fetch_result={"result": [PHISHING]}, tool_errors={"apply_label"})

    await _run(settings, session, patch_mcp_session)

    # apply_label reported is_error, but move_email was still attempted.
    assert session.calls_to("move_email") == [{"email_id": "1", "folder": "Quarantine"}]


@respx.mock
async def test_every_email_in_the_batch_is_processed(settings, patch_mcp_session):
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=PHISHING_VERDICT))
    respx.post(CHAT_URL).mock(return_value=_chat(QUARANTINE_DECISION))
    session = FakeMcpSession(fetch_result={"result": [PHISHING, {**PHISHING, "id": "2"}]})

    await _run(settings, session, patch_mcp_session)

    assert len(session.calls_to("apply_label")) == 2


class TestBackoff:
    def test_a_healthy_cycle_waits_the_poll_interval(self, settings):
        assert _backoff_seconds(settings, 0) == 60

    def test_delay_doubles_while_failing(self, settings):
        assert [_backoff_seconds(settings, n) for n in (1, 2, 3)] == [120, 240, 480]

    def test_delay_is_capped(self, settings):
        assert _backoff_seconds(settings, 99) == 15 * 60


class TestTransportSelection:
    """`open_mcp_session` picks the transport; mcp-server-rust only speaks SSE."""

    async def test_auto_uses_the_streamable_http_client(self, monkeypatch):
        import mcp_client.client as client_module

        seen = {}

        class FakeClient:
            def __init__(self, url):
                seen["url"] = url

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(client_module, "Client", FakeClient)
        monkeypatch.setenv("MCP_TRANSPORT", "auto")

        async with client_module.open_mcp_session(load_settings()) as session:
            assert isinstance(session, FakeClient)
        assert seen["url"] == "http://mcp.test:8080/mcp"

    async def test_sse_opens_the_legacy_transport_and_initializes(self, monkeypatch):
        from contextlib import asynccontextmanager

        import mcp_client.client as client_module

        seen = {}

        @asynccontextmanager
        async def fake_sse_client(url):
            seen["url"] = url
            yield ("read", "write")

        class FakeSession:
            def __init__(self, read, write):
                seen["streams"] = (read, write)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def initialize(self):
                seen["initialized"] = True

        monkeypatch.setattr(client_module, "sse_client", fake_sse_client)
        monkeypatch.setattr(client_module, "ClientSession", FakeSession)
        monkeypatch.setenv("MCP_TRANSPORT", "sse")

        async with client_module.open_mcp_session(load_settings()) as session:
            assert isinstance(session, FakeSession)

        assert seen["url"] == "http://mcp.test:8080/mcp"
        assert seen["streams"] == ("read", "write")
        assert seen["initialized"] is True
