from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic
import httpx
from mcp.client import Client

from .ai_service import AIServiceError, analyze_email
from .config import Settings, load_settings
from .mcp_tools import call_tool_result_to_content, to_anthropic_tools, unwrap_list_result
from .ollama_decider import OllamaDecider, OllamaError
from .schemas import AnalysisResult

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

CLAUDE_SYSTEM_PROMPT = """You are the triage agent for AGUERO, an email security and intelligence \
platform. For each email you are shown, you will also be given a structured threat analysis \
(threat level, urgency, category) that has already been produced by the AI service. Your job is \
to decide, using the available tools, what action (if any) to take on that specific email:

- If threat_level is "Phishing" or "Spam": apply_label with a label that names the threat \
  (e.g. "Phishing" or "Spam"), and move_email to the "Quarantine" folder.
- If urgency is "Critical" and the email is not already being quarantined as a threat: \
  apply_label "Critical" so it is not missed.
- Safe, routine, or promotional email generally needs no action; you may apply_label \
  "Promotion" or "Work" only if it clearly helps the user's inbox stay organized.
- The email has already been fetched -- do not call fetch_unread_emails yourself.
- Only call tools you have clear justification for from the analysis given. When you are \
  done, reply with one short sentence summarizing what you did (or that you took no action).
"""


async def _handle_email_with_claude(
    anthropic_client: anthropic.AsyncAnthropic,
    mcp_client: Client,
    tools: list[dict],
    model: str,
    email: dict[str, Any],
    analysis: AnalysisResult,
) -> None:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": json.dumps({"email": email, "analysis": analysis.model_dump()}),
        }
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await anthropic_client.messages.create(
            model=model,
            max_tokens=1024,
            system=CLAUDE_SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = await mcp_client.call_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": call_tool_result_to_content(result),
                        "is_error": result.is_error,
                    }
                )
            except Exception as exc:  # MCP call itself failed (network, server error)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Tool call failed: {exc}",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})


async def _handle_email_with_ollama(
    ollama_decider: OllamaDecider,
    mcp_client: Client,
    tools: list[dict[str, Any]],
    email: dict[str, Any],
    analysis: AnalysisResult,
) -> None:
    valid_tool_names = {t["name"] for t in tools}
    try:
        decision = await ollama_decider.decide(tools, email, analysis.model_dump())
    except OllamaError:
        logger.exception(
            "Ollama decision failed for email %s; taking no action", email.get("id")
        )
        return

    logger.info("Ollama decision for email %s: %s", email.get("id"), decision.get("reason"))

    for action in decision["actions"]:
        tool_name = action.get("tool")
        arguments = action.get("arguments") or {}
        if tool_name not in valid_tool_names:
            logger.warning("Ollama chose an unknown tool %r; skipping", tool_name)
            continue
        try:
            result = await mcp_client.call_tool(tool_name, arguments)
            if result.is_error:
                logger.warning(
                    "Tool %s reported an error for email %s: %s",
                    tool_name,
                    email.get("id"),
                    call_tool_result_to_content(result),
                )
        except Exception:
            logger.exception("Tool call %s failed for email %s", tool_name, email.get("id"))


async def handle_email(
    settings: Settings,
    anthropic_client: anthropic.AsyncAnthropic | None,
    ollama_decider: OllamaDecider | None,
    mcp_client: Client,
    tools: list[dict[str, Any]],
    email: dict[str, Any],
    analysis: AnalysisResult,
) -> None:
    if settings.llm_provider == "ollama":
        assert ollama_decider is not None
        await _handle_email_with_ollama(ollama_decider, mcp_client, tools, email, analysis)
    else:
        assert anthropic_client is not None
        await _handle_email_with_claude(
            anthropic_client, mcp_client, tools, settings.claude_model, email, analysis
        )


async def run_once(
    settings: Settings,
    anthropic_client: anthropic.AsyncAnthropic | None,
    ollama_decider: OllamaDecider | None,
    http_client: httpx.AsyncClient,
) -> None:
    async with Client(settings.mcp_server_url) as mcp_client:
        tools = to_anthropic_tools((await mcp_client.list_tools()).tools)

        fetched = await mcp_client.call_tool("fetch_unread_emails", {})
        emails = unwrap_list_result(fetched.structured_content)
        logger.info("Fetched %d unread email(s)", len(emails))

        for email in emails:
            try:
                analysis = await analyze_email(http_client, settings, email)
            except AIServiceError:
                logger.exception("AI analysis failed for email %s; skipping", email.get("id"))
                continue

            await handle_email(
                settings, anthropic_client, ollama_decider, mcp_client, tools, email, analysis
            )


async def run() -> None:
    settings = load_settings()
    anthropic_client = (
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        if settings.llm_provider == "anthropic"
        else None
    )

    async with httpx.AsyncClient() as http_client:
        ollama_decider = OllamaDecider(settings, http_client) if settings.llm_provider == "ollama" else None
        while True:
            try:
                await run_once(settings, anthropic_client, ollama_decider, http_client)
            except Exception:
                logger.exception("Poll cycle failed; will retry next interval")
            await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
