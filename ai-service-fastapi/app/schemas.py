from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ThreatLevel(str, Enum):
    SAFE = "Safe"
    PHISHING = "Phishing"
    SPAM = "Spam"


class Urgency(str, Enum):
    CRITICAL = "Critical"
    NORMAL = "Normal"
    LOW = "Low"


class Category(str, Enum):
    PROMOTION = "Promotion"
    UPDATE = "Update"
    WORK = "Work"


class AnalysisSource(str, Enum):
    """Which classifier produced a result."""

    OLLAMA = "ollama"
    HEURISTIC = "heuristic"


class AnalyzeRequest(BaseModel):
    content: str = Field(
        ...,
        description="Raw email text, as assembled by the orchestrator "
        "(From/Subject headers followed by the body or snippet).",
    )


class AnalysisResult(BaseModel):
    """Response contract of POST /analyze.

    The first three fields are the contract mcp-client-python validates against
    (see mcp_client/schemas.py). `reason` and `source` are additive: pydantic
    ignores unknown fields by default, so the orchestrator is unaffected by them
    while humans reading logs get to see why a verdict was reached.
    """

    threat_level: ThreatLevel
    urgency: Urgency
    category: Category
    reason: str = ""
    source: AnalysisSource = AnalysisSource.OLLAMA


class HealthResponse(BaseModel):
    # `model*` fields collide with pydantic's protected namespace otherwise.
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model: str
    ollama_base_url: str
    ollama_reachable: bool
    model_available: bool | None = None
    detail: str | None = None
