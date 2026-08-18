from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from .config import Settings
from .normalize import normalize_verdict
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, build_response_schema
from .schemas import AnalysisResult, AnalysisSource

logger = logging.getLogger(__name__)

class OllamaError(RuntimeError):
    """Ollama was unreachable, too slow, or returned something unusable."""


class OllamaClient:
    """Thin wrapper over Ollama's /api/chat with structured (JSON schema) output.

    Deliberately talks to the HTTP API directly rather than pulling in the
    `ollama` SDK: the surface used here is two endpoints, and httpx is already a
    dependency of the FastAPI stack.
    """

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client
        self._schema = build_response_schema()

    @property
    def _headers(self) -> dict[str, str]:
        # ollama.com needs a bearer token; a local daemon ignores the header.
        if self._settings.ollama_api_key:
            return {"Authorization": f"Bearer {self._settings.ollama_api_key}"}
        return {}

    async def analyze(self, content: str, *, unbounded: bool = False) -> AnalysisResult:
        """Classify one email.

        `unbounded` drops the configured timeout, and is only for warm-up: a
        real request must stay inside the orchestrator's budget.
        """
        settings = self._settings
        truncated = content[: settings.max_content_chars]
        request_timeout = None if unbounded else settings.ollama_timeout_seconds

        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(content=truncated)},
            ],
            "stream": False,
            "format": self._schema if settings.ollama_format_mode == "schema" else "json",
            "options": {
                # Classification, not creative writing: pin decoding down so the
                # same email always gets the same verdict.
                "temperature": 0,
                "top_p": 1,
                "num_ctx": settings.ollama_num_ctx,
            },
        }

        # Only sent when configured: models without thinking reject the field.
        if settings.think is not None:
            payload["think"] = settings.think

        try:
            response = await self._http.post(
                f"{settings.base_url}/api/chat",
                json=payload,
                headers=self._headers,
                timeout=request_timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Ollama did not answer within {request_timeout}s") from exc
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

        try:
            result = AnalysisResult.model_validate(
                {**normalize_verdict(parsed), "source": AnalysisSource.OLLAMA}
            )
        except ValidationError as exc:
            raise OllamaError(f"Ollama returned an off-contract verdict: {exc}") from exc

        logger.debug("Ollama verdict: %s", result.model_dump())
        return result

    async def list_models(self) -> list[str]:
        """Model names Ollama currently has available; used by /health."""
        response = await self._http.get(
            f"{self._settings.base_url}/api/tags",
            headers=self._headers,
            timeout=self._settings.ollama_timeout_seconds,
        )
        response.raise_for_status()
        return [model.get("name", "") for model in response.json().get("models", [])]
