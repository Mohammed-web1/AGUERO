from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

# Allow the frontend dashboard (any origin) to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Reject oversized bodies before they are read into memory.

    `max_content_chars` truncates the email text, but only once the whole body
    has been buffered -- so a 20MB POST was previously read in full and then
    discarded down to 6000 characters.

    This checks the declared Content-Length, which covers any ordinary client.
    A chunked request without that header still gets through to be buffered; a
    hard byte cap belongs in the reverse proxy in front of this service, which
    is the layer that can stop reading mid-stream.
    """
    settings = get_settings()
    declared = request.headers.get("content-length")

    if declared is not None:
        try:
            body_bytes = int(declared)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

        if body_bytes > settings.max_request_bytes:
            logger.warning(
                "Rejected a %d byte request; limit is %d", body_bytes, settings.max_request_bytes
            )
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "detail": f"Request body of {body_bytes} bytes exceeds the "
                    f"{settings.max_request_bytes} byte limit"
                },
            )

    return await call_next(request)


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
    except OllamaError as exc:
        # Every way of not getting a usable model list arrives as OllamaError,
        # including a reachable endpoint answering with non-JSON. Reporting
        # that as 500 would mark the container unhealthy over a degradation
        # this service is designed to absorb.
        degraded_ok = settings.enable_heuristic_fallback
        return HealthResponse(
            status="degraded" if degraded_ok else "unhealthy",
            model=settings.ollama_model,
            ollama_base_url=settings.base_url,
            ollama_reachable=False,
            detail=f"Cannot use Ollama: {exc}",
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
