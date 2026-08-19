"""The orchestrator's side of the POST /analyze contract."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from mcp_client.ai_service import AIServiceError, analyze_email
from mcp_client.schemas import Category, ThreatLevel, Urgency

ANALYZE_URL = "http://ai.test:8000/analyze"

EMAIL = {
    "id": "1",
    "sender": "security@paypa1-verify.com",
    "subject": "Verify your account",
    "snippet": "Your account will be suspended.",
}

VERDICT = {"threat_level": "Phishing", "urgency": "Critical", "category": "Update"}


@respx.mock
async def test_verdict_is_parsed_onto_the_contract(settings):
    respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=VERDICT))

    async with httpx.AsyncClient() as http_client:
        result = await analyze_email(http_client, settings, EMAIL)

    assert result.threat_level is ThreatLevel.PHISHING
    assert result.urgency is Urgency.CRITICAL
    assert result.category is Category.UPDATE


@respx.mock
async def test_headers_and_body_are_assembled_for_the_model(settings):
    route = respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=VERDICT))

    async with httpx.AsyncClient() as http_client:
        await analyze_email(http_client, settings, EMAIL)

    content = json.loads(route.calls.last.request.content)["content"]
    assert content.startswith("From: security@paypa1-verify.com\nSubject: Verify your account")
    assert "Your account will be suspended." in content


@respx.mock
async def test_body_is_used_when_there_is_no_snippet(settings):
    route = respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=VERDICT))
    email = {"id": "2", "sender": "a@b.test", "subject": "s", "body": "full body here"}

    async with httpx.AsyncClient() as http_client:
        await analyze_email(http_client, settings, email)

    assert "full body here" in json.loads(route.calls.last.request.content)["content"]


@respx.mock
async def test_an_id_only_email_still_produces_a_request(settings):
    """mcp-server-rust returns ids with no content; that must not crash the cycle."""
    route = respx.post(ANALYZE_URL).mock(return_value=httpx.Response(200, json=VERDICT))

    async with httpx.AsyncClient() as http_client:
        await analyze_email(http_client, settings, {"id": "3"})

    assert json.loads(route.calls.last.request.content)["content"] == "From: \nSubject: \n\n"


@respx.mock
async def test_extra_response_fields_are_ignored(settings):
    """`reason` and `source` are additive; the orchestrator must not break on them."""
    respx.post(ANALYZE_URL).mock(
        return_value=httpx.Response(200, json={**VERDICT, "reason": "why", "source": "heuristic"})
    )

    async with httpx.AsyncClient() as http_client:
        result = await analyze_email(http_client, settings, EMAIL)

    assert result.threat_level is ThreatLevel.PHISHING


@respx.mock
@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json={"threat_level": "Nuclear"}),
        httpx.Response(200, json={}),
    ],
    ids=["http-error", "off-contract-enum", "missing-fields"],
)
async def test_bad_responses_raise_ai_service_error(settings, failure):
    respx.post(ANALYZE_URL).mock(return_value=failure)

    async with httpx.AsyncClient() as http_client:
        with pytest.raises(AIServiceError):
            await analyze_email(http_client, settings, EMAIL)


@respx.mock
async def test_transport_errors_raise_ai_service_error(settings):
    respx.post(ANALYZE_URL).mock(side_effect=httpx.ConnectError("refused"))

    async with httpx.AsyncClient() as http_client:
        with pytest.raises(AIServiceError, match="'1'"):
            await analyze_email(http_client, settings, EMAIL)
