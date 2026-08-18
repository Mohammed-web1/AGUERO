from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from .config import Settings
from .schemas import AnalysisResult


class AIServiceError(RuntimeError):
    """Raised when the AI service is unreachable or returns an unexpected response."""


async def analyze_email(
    http_client: httpx.AsyncClient, settings: Settings, email: dict[str, Any]
) -> AnalysisResult:
    content = (
        f"From: {email.get('sender', '')}\n"
        f"Subject: {email.get('subject', '')}\n\n"
        f"{email.get('snippet') or email.get('body', '')}"
    )
    try:
        response = await http_client.post(
            f"{settings.ai_service_url}/analyze", json={"content": content}, timeout=10.0
        )
        response.raise_for_status()
        return AnalysisResult.model_validate(response.json())
    except (httpx.HTTPError, ValidationError) as exc:
        raise AIServiceError(
            f"AI service call failed for email {email.get('id')!r}: {exc}"
        ) from exc
