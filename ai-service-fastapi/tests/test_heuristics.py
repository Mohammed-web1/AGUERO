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


@pytest.mark.parametrize(
    "content",
    [
        "I was wondering about the sprint deadline",
        "That was a wonderful presentation, thanks",
        "He won't be able to join the standup",
        "He won’t be able to join the standup",
        "The wonton soup place near the office is good",
        "Please review the contract now that legal signed off",
        "We need an exact now-or-never decision on the vendor",
    ],
    ids=[
        "wondering",
        "wonderful",
        "wont-apostrophe",
        "wont-smart-quote",
        "wonton",
        "contract-now",
        "exact-now",
    ],
)
def test_ordinary_words_do_not_register_as_spam_signals(content):
    """Substring matching used to fire on "wondering", "won't" and "contract now".

    Two spam hits flip the verdict, so unanchored patterns like these are how a
    perfectly ordinary work email gets filed as junk.
    """
    from app.heuristics import SPAM_PATTERNS, _hits

    assert _hits(SPAM_PATTERNS, content.lower()) == []


@pytest.mark.parametrize(
    "content",
    [
        "You have won $5,000,000 in the international lottery",
        "YOU'VE WON! Claim your prize now",
        "Limited time offer, act now",
    ],
    ids=["you-have-won", "youve-won", "act-now"],
)
def test_real_spam_phrasing_still_matches(content):
    """The narrowing must not cost the detections the patterns exist for."""
    from app.heuristics import SPAM_PATTERNS, _hits

    assert _hits(SPAM_PATTERNS, content.lower()) != []


def test_a_work_email_mentioning_a_contract_stays_safe():
    """End to end: the false positives above must not reach the verdict."""
    result = classify(
        "From: legal@acme.com\nSubject: Vendor contract\n\n"
        "Please review the contract now that legal signed off. I was wondering "
        "whether the sprint deadline still holds."
    )
    assert result.threat_level is ThreatLevel.SAFE
