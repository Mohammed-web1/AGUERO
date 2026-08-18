from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Ollama was unreachable, too slow, or returned something unusable."""


SYSTEM_PROMPT = """You are the triage agent for AGUERO, an email security and intelligence \
platform. For each email you are shown, you will also be given a structured threat analysis \
(threat level, urgency, category) that has already been produced by the AI service, and the list \
of tools available to you (name, description, and JSON Schema of their arguments). Decide what \
action (if any) to take on that specific email:

- If threat_level is "Phishing" or "Spam": call apply_label with a label that names the threat \
  (e.g. "Phishing" or "Spam"), and call move_email to the "Quarantine" folder.
- If urgency is "Critical" and the email is not already being quarantined as a threat: call \
  apply_label "Critical" so it is not missed.
- Safe, routine, or promotional email generally needs no action; you may apply_label \
  "Promotion" or "Work" only if it clearly helps the user's inbox stay organized.
- Only call tools you have clear justification for from the analysis given.

Reply with JSON only, matching this shape:
{"actions": [{"tool": "<tool name>", "arguments": {...}}, ...], "reason": "<one short sentence>"}
Use an empty "actions" list if no action is warranted.
"""


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["tool", "arguments"],
                },
            },
            "reason": {"type": "string"},
        },
        "required": ["actions", "reason"],
    }


class OllamaDecider:
    """Decides label/move actions for one email via Ollama's /api/chat, structured JSON output.

    Deliberately not native Ollama tool-calling: tool-calling support varies across models, while
    structured JSON output plus the orchestrator executing the chosen tools itself is reliable and
    mirrors the pattern ai-service-fastapi already uses for its own Ollama calls.
    """

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    @property
    def _headers(self) -> dict[str, str]:
        if self._settings.ollama_api_key:
            return {"Authorization": f"Bearer {self._settings.ollama_api_key}"}
        return {}

    async def decide(
        self,
        tools: list[dict[str, Any]],
        email: dict[str, Any],
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        settings = self._settings
        tool_descriptions = "\n".join(
            f"- {t['name']}: {t['description']} (arguments schema: {json.dumps(t['input_schema'])})"
            for t in tools
        )
        user_prompt = (
            f"Available tools:\n{tool_descriptions}\n\n"
            f"Email:\n{json.dumps(email)}\n\n"
            f"Analysis:\n{json.dumps(analysis)}\n\n"
            "Decide which tools to call, if any. Reply with JSON only."
        )

        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": _response_schema() if settings.ollama_format_mode == "schema" else "json",
            "options": {"temperature": 0, "top_p": 1},
        }
        if settings.think is not None:
            payload["think"] = settings.think

        try:
            response = await self._http.post(
                f"{settings.ollama_base_url}/api/chat",
                json=payload,
                headers=self._headers,
                timeout=settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama did not answer within {settings.ollama_timeout_seconds}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

        raw = (body.get("message") or {}).get("content", "")
        if not raw:
            raise OllamaError("Ollama returned an empty message")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama did not return JSON: {raw[:200]!r}") from exc

        if not isinstance(parsed.get("actions"), list):
            raise OllamaError(f"Ollama response is missing an 'actions' list: {parsed!r}")

        return parsed
