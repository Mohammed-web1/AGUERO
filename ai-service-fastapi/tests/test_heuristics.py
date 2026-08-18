from __future__ import annotations

import pytest

from app.heuristics import classify
from app.schemas import AnalysisSource, Category, ThreatLevel, Urgency


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Subject: Please verify your account now", ThreatLevel.PHISHING),
        ("Your package could not be delivered, click here to reschedule", ThreatLevel.PHISHING),
        ("You have won! Claim your prize, risk-free, act now", ThreatLevel.SPAM),
        ("Subject: Lunch tomorrow?\n\nAre you free at noon?", ThreatLevel.SAFE),
    ],
)
def test_threat_level(content, expected):
    assert classify(content).threat_level is expected


def test_phishing_is_always_critical():
    result = classify("Confirm your password or your account will be suspended")
    assert result.urgency is Urgency.CRITICAL


def test_promotional_mail_is_low_urgency_and_promotion():
    result = classify("50% off everything! Huge sale, free shipping. Unsubscribe here to stop")
    assert result.urgency is Urgency.LOW
    assert result.category is Category.PROMOTION


def test_transactional_mail_is_update():
    result = classify(
        "From: no-reply@shop.example\nSubject: Your receipt\n\nOrder confirmation: it has shipped."
    )
    assert result.category is Category.UPDATE
    assert result.threat_level is ThreatLevel.SAFE


def test_unmatched_mail_defaults_to_safe_normal_work():
    result = classify("hey, thoughts on the draft?")
    assert (result.threat_level, result.urgency, result.category) == (
        ThreatLevel.SAFE,
        Urgency.NORMAL,
        Category.WORK,
    )


def test_results_are_tagged_as_heuristic():
    assert classify("anything").source is AnalysisSource.HEURISTIC
