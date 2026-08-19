from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Protocol

import anthropic
import httpx
from mcp.client import Client, ClientSession
from mcp.client.sse import sse_client

from .ai_service import AIServiceError, analyze_email
from .config import Settings, load_settings
from .mcp_tools import call_tool_result_to_content, to_anthropic_tools, unwrap_list_result
from .ollama_decider import OllamaDecider, OllamaError
from .schemas import AnalysisResult

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# A failing poll cycle backs off exponentially rather than hammering a server
# that is down, but never waits longer than this between attempts.
MAX_BACKOFF_SECONDS = 15 * 60


class McpSession(Protocol):
    """The slice of the MCP client API this orchestrator uses.

    Both `mcp.client.Client` and `mcp.client.ClientSession` satisfy it, which is
    what lets the transport be chosen at runtime.
    """

    async def list_tools(self, *args: Any, **kwargs: Any) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


@asynccontextmanager
async def open_mcp_session(settings: Settings) -> AsyncIterator[McpSession]:
    """Open an MCP session using the configured transport.

    `auto` hands the URL to the SDK, which negotiates Streamable HTTP. `sse`
    opens the older HTTP+SSE transport explicitly; the SDK does not fall back to
    it on its own, so a server that only speaks SSE needs it named.
    """
    if settings.mcp_transport == "sse":
        async with sse_client(settings.mcp_server_url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
    else:
        async with Client(settings.mcp_server_url) as client:
            yield client


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
    mcp_client: McpSession,
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
    else:
        # Budget exhausted with Claude still asking for tools. The calls made so
        # far already took effect, so say so rather than letting the truncation
        # look like a clean finish.
        logger.warning(
            "Claude still wanted tools after %d iterations for email %s; "
            "stopping with its actions so far applied",
            MAX_TOOL_ITERATIONS,
            email.get("id"),
        )


async def _handle_email_with_ollama(
    ollama_decider: OllamaDecider,
    mcp_client: McpSession,
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
    mcp_client: McpSession,
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
    async with open_mcp_session(settings) as mcp_client:
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


def _backoff_seconds(settings: Settings, consecutive_failures: int) -> int:
    """Delay before the next cycle: the poll interval, doubling while failing.

    A dead MCP server or AI service is the common failure here, and retrying
    every 60s indefinitely just fills the log. Capped so recovery is still
    picked up within a reasonable window.
    """
    if consecutive_failures == 0:
        return settings.poll_interval_seconds
    delay = settings.poll_interval_seconds * 2**consecutive_failures
    return min(delay, MAX_BACKOFF_SECONDS)


async def run() -> None:
    settings = load_settings()

    async with AsyncExitStack() as stack:
        http_client = await stack.enter_async_context(httpx.AsyncClient())

        anthropic_client: anthropic.AsyncAnthropic | None = None
        ollama_decider: OllamaDecider | None = None
        if settings.llm_provider == "anthropic":
            anthropic_client = await stack.enter_async_context(
                anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            )
        else:
            ollama_decider = OllamaDecider(settings, http_client)

        consecutive_failures = 0
        while True:
            try:
                await run_once(settings, anthropic_client, ollama_decider, http_client)
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "Poll cycle failed (%d in a row); retrying in %ds",
                    consecutive_failures,
                    _backoff_seconds(settings, consecutive_failures),
                )
            await asyncio.sleep(_backoff_seconds(settings, consecutive_failures))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
