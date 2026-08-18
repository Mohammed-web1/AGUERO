from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


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


class AnalysisResult(BaseModel):
    """Response contract of ai-service-fastapi's POST /analyze.

    ai-service-fastapi has no implementation yet; this shape is inferred from
    its README and will need reconciling once the real service exists.
    """

    threat_level: ThreatLevel
    urgency: Urgency
    category: Category
