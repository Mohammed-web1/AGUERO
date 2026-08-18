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

    Deliberately duplicated rather than imported: the two services are separate
    deployables with separate dependency sets, and this is the boundary each
    validates independently. The service also returns `reason` and `source`,
    which pydantic ignores here. Keep the enum values in step with
    ai-service-fastapi/app/schemas.py.
    """

    threat_level: ThreatLevel
    urgency: Urgency
    category: Category
