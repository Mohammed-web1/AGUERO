from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status

from . import heuristics
from .config import Settings, get_settings
from .ollama_client import OllamaClient, OllamaError
from .schemas import AnalysisResult, AnalyzeRequest, HealthResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "AI service starting: model=%s ollama=%s fallback=%s",
        settings.ollama_model,
        settings.base_url,
        settings.enable_heuristic_fallback,
    )

    # One connection pool for the process; keeping it warm matters because the
    # orchestrator calls /analyze once per email on every poll cycle.
    async with httpx.AsyncClient() as http_client:
        app.state.http_client = http_client
        app.state.ollama = OllamaClient(settings, http_client)
        yield

    logger.info("AI service stopped")


app = FastAPI(
    title="AGUERO AI Intelligence Engine",
    description=(
        "Classifies email on three axes -- threat level, urgency and category -- "
        "using an Ollama-hosted LLM with a keyword fallback."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def get_ollama(request: Request) -> OllamaClient:
    return request.app.state.ollama


@app.get("/health", response_model=HealthResponse)
async def health(
    settings: Settings = Depends(get_settings),
    ollama: OllamaClient = Depends(get_ollama),
) -> HealthResponse:
    """Liveness plus a real check that the configured model is actually there.

    Returns 200 even when Ollama is down, as long as the heuristic fallback can
    still answer -- the service is degraded, not dead. `status` says which.
    """
    try:
        models = await ollama.list_models()
    except httpx.HTTPError as exc:
        degraded_ok = settings.enable_heuristic_fallback
        return HealthResponse(
            status="degraded" if degraded_ok else "unhealthy",
            model=settings.ollama_model,
            ollama_base_url=settings.base_url,
            ollama_reachable=False,
            # httpx connection errors often stringify to "", so name the type.
            detail=f"Cannot reach Ollama: {type(exc).__name__}: {exc}".rstrip(": "),
        )

    # Ollama reports tags as "name:tag"; an untagged config means ":latest".
    wanted = settings.ollama_model
    available = wanted in models or f"{wanted}:latest" in models
    return HealthResponse(
        status="ok" if available else "degraded",
        model=wanted,
        ollama_base_url=settings.base_url,
        ollama_reachable=True,
        model_available=available,
        detail=None if available else f"Model {wanted!r} not pulled. Available: {models or 'none'}",
    )


@app.post("/analyze", response_model=AnalysisResult)
async def analyze(
    payload: AnalyzeRequest,
    settings: Settings = Depends(get_settings),
    ollama: OllamaClient = Depends(get_ollama),
) -> AnalysisResult:
    """Classify one email. This is the endpoint mcp-client-python polls against."""
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="content must not be empty",
        )

    try:
        return await ollama.analyze(content)
    except OllamaError as exc:
        if not settings.enable_heuristic_fallback:
            logger.error("Analysis failed and fallback is disabled: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Model analysis unavailable: {exc}",
            ) from exc

        logger.warning("Ollama analysis failed (%s); falling back to heuristics", exc)
        return heuristics.classify(content)
