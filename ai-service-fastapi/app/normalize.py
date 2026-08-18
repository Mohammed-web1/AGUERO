"""Coerce a model's verdict onto the contract's enum values.

Only needed for `OLLAMA_FORMAT_MODE=json`: plain JSON mode guarantees valid
JSON but not valid *values*, so a model may answer "high" where the contract
says "Critical". The prompt already states the allowed values and shows worked
examples, so this is insurance rather than the main mechanism -- anything it
cannot map confidently is left to fail validation, which routes the email to
the heuristic fallback instead of inventing a verdict.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from .schemas import Category, ThreatLevel, Urgency

# Synonyms grouped by the contract value they mean. Inverted into a lookup
# below, so the canonical strings come from the enums and are never retyped.
_SYNONYMS: dict[Enum, tuple[str, ...]] = {
    ThreatLevel.SAFE: (
        "safe", "benign", "legitimate", "legit", "clean", "ham", "none", "nothreat", "ok",
    ),
    ThreatLevel.PHISHING: (
        "phishing", "phish", "malware", "malicious", "fraud", "fraudulent", "scam",
        "spoofing", "credentialharvesting",
    ),
    ThreatLevel.SPAM: ("spam", "junk", "bulk", "unsolicited"),
    Urgency.CRITICAL: ("critical", "high", "urgent", "immediate", "severe"),
    Urgency.NORMAL: ("normal", "medium", "moderate", "standard", "routine"),
    Urgency.LOW: ("low", "none", "minor", "informational", "info"),
    Category.PROMOTION: (
        "promotion", "promotional", "promo", "marketing", "advertisement", "ad", "newsletter",
    ),
    Category.UPDATE: (
        "update", "transactional", "notification", "notice", "automated", "alert", "system",
    ),
    Category.WORK: ("work", "business", "correspondence", "professional", "personal"),
}


def _lookup(enum: type[Enum]) -> dict[str, str]:
    """Synonym -> canonical value, for the members of one enum."""
    return {
        synonym: member.value
        for member, synonyms in _SYNONYMS.items()
        if isinstance(member, enum)
        for synonym in synonyms
    }


# "none" and "low" mean different things per axis, hence a table per field.
_FIELDS = {
    "threat_level": _lookup(ThreatLevel),
    "urgency": _lookup(Urgency),
    "category": _lookup(Category),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_verdict(parsed: dict[str, Any]) -> dict[str, Any]:
    """Map recognised synonyms onto contract values, leaving the rest untouched."""
    result = dict(parsed)
    for field, mapping in _FIELDS.items():
        value = result.get(field)
        if isinstance(value, str):
            # Unmapped values pass through unchanged and fail validation later.
            result[field] = mapping.get(_key(value), value)

    reason = result.get("reason")
    if not isinstance(reason, str):
        result["reason"] = ""
    return result
