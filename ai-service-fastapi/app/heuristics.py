"""Keyword fallback classifier.

Used when Ollama is unreachable, too slow, or answers off-contract. It is
deliberately crude -- its job is to keep the orchestrator's poll cycle moving
with a defensible verdict rather than to compete with the model. Results are
tagged `source: "heuristic"` so a degraded pipeline is visible in the logs.
"""

from __future__ import annotations

import re

from .schemas import AnalysisResult, AnalysisSource, Category, ThreatLevel, Urgency

PHISHING_PATTERNS = (
    r"verify your (account|identity|email)",
    r"confirm your (account|password|identity|payment|details)",
    r"(account|password) (will be |is )?(suspend|expir|lock|disabl|deactivat)",
    r"unusual (sign[- ]?in|login|activity)",
    r"click (here|below|the link) (to|and)",
    r"update your (billing|payment|card|bank)",
    r"(wire|transfer) (the |these )?funds",
    r"gift card",
    r"(send|share) (me |us )?your (password|otp|mfa|2fa|one[- ]time code)",
    r"(final|last) (notice|warning)",
    r"your (package|parcel|delivery) (could not|failed to) be deliver",
)

SPAM_PATTERNS = (
    r"(you('ve| have) )?won",
    r"(claim|collect) your (prize|reward|bonus)",
    r"risk[- ]free",
    r"work from home",
    r"crypto(currency)? (investment|opportunity|signal)",
    r"(viagra|cialis|weight loss|miracle cure)",
    r"limited time offer",
    r"act now",
    r"unsubscribe here to stop",
)

CRITICAL_PATTERNS = (
    r"\burgent\b",
    r"\basap\b",
    r"immediate(ly)? (action|attention|response)",
    r"(production|prod) (outage|incident|down)",
    r"\bsev[- ]?[12]\b",
    r"(security|breach) (alert|incident)",
    r"deadline (is )?(today|tomorrow)",
    r"(due|expires) (today|tomorrow)",
    r"time[- ]sensitive",
)

PROMOTION_PATTERNS = (
    r"\d+% off",
    r"\bsale\b",
    r"\bdeal(s)?\b",
    r"\bdiscount\b",
    r"\bnewsletter\b",
    r"\bcoupon\b",
    r"\bblack friday\b",
    r"free shipping",
    r"shop now",
    r"unsubscribe",
)

UPDATE_PATTERNS = (
    r"\breceipt\b",
    r"\binvoice #",
    r"order (confirm|#|number)",
    r"(has )?shipped\b",
    r"out for delivery",
    r"password reset",
    r"(verification|security) code",
    r"your (subscription|plan) (has|will)",
    r"no[- ]reply@",
    r"do not reply to this (email|message)",
)

WORK_PATTERNS = (
    r"\bmeeting\b",
    r"\bstandup\b",
    r"\bsprint\b",
    r"\bdeadline\b",
    r"pull request",
    r"\bproposal\b",
    r"\bcontract\b",
    r"\bclient\b",
    r"per my last email",
    r"(please|kindly) (review|confirm|approve)",
)


def _hits(patterns: tuple[str, ...], text: str) -> list[str]:
    """Return the matched email text, not the patterns -- reasons stay readable."""
    matches = []
    for pattern in patterns:
        found = re.search(pattern, text)
        if found:
            matches.append(found.group(0).strip())
    return matches


def classify(content: str) -> AnalysisResult:
    text = content.lower()

    phishing = _hits(PHISHING_PATTERNS, text)
    spam = _hits(SPAM_PATTERNS, text)
    critical = _hits(CRITICAL_PATTERNS, text)
    promotion = _hits(PROMOTION_PATTERNS, text)
    update = _hits(UPDATE_PATTERNS, text)
    work = _hits(WORK_PATTERNS, text)

    if phishing:
        threat, evidence = ThreatLevel.PHISHING, phishing
    elif len(spam) >= 2:
        threat, evidence = ThreatLevel.SPAM, spam
    else:
        threat, evidence = ThreatLevel.SAFE, []

    # Phishing is Critical so a human sees it before acting on it.
    if threat is ThreatLevel.PHISHING or critical:
        urgency = Urgency.CRITICAL
    elif threat is ThreatLevel.SPAM or len(promotion) >= 2:
        urgency = Urgency.LOW
    else:
        urgency = Urgency.NORMAL

    scores = {
        Category.PROMOTION: len(promotion),
        Category.UPDATE: len(update),
        Category.WORK: len(work),
    }
    category = max(scores, key=lambda key: scores[key])
    if scores[category] == 0:
        # Nothing matched: Work is the least destructive default, since the
        # orchestrator leaves Work mail alone rather than filing it away.
        category = Category.WORK

    matched = evidence or critical or promotion or update or work
    if matched:
        # Quoted email text, so cap it: this is untrusted input being echoed back.
        quoted = ", ".join(repr(m[:40]) for m in matched[:3])
        reason = f"Heuristic fallback matched {len(matched)} signal(s): {quoted}."
    else:
        reason = "Heuristic fallback found no notable signals; defaulted to safe routine mail."

    return AnalysisResult(
        threat_level=threat,
        urgency=urgency,
        category=category,
        reason=reason,
        source=AnalysisSource.HEURISTIC,
    )
