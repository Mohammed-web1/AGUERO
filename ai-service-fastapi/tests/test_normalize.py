from __future__ import annotations

import pytest

from app.normalize import normalize_verdict


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"threat_level": "phishing"}, "Phishing"),
        ({"threat_level": "PHISHING"}, "Phishing"),
        ({"threat_level": "malicious"}, "Phishing"),
        ({"threat_level": "credential harvesting"}, "Phishing"),
        ({"threat_level": "legitimate"}, "Safe"),
        ({"threat_level": "junk"}, "Spam"),
    ],
)
def test_threat_synonyms(raw, expected):
    assert normalize_verdict(raw)["threat_level"] == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("high", "Critical"), ("urgent", "Critical"), ("medium", "Normal"), ("info", "Low")],
)
def test_urgency_synonyms(raw, expected):
    assert normalize_verdict({"urgency": raw})["urgency"] == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("marketing", "Promotion"), ("transactional", "Update"), ("business", "Work")],
)
def test_category_synonyms(raw, expected):
    assert normalize_verdict({"category": raw})["category"] == expected


def test_contract_values_are_left_alone():
    verdict = {"threat_level": "Safe", "urgency": "Normal", "category": "Work", "reason": "x"}
    assert normalize_verdict(verdict) == verdict


def test_unmappable_values_pass_through_to_fail_validation():
    """Better to fail into the heuristic fallback than to guess a verdict."""
    assert normalize_verdict({"threat_level": "extremely spicy"})["threat_level"] == (
        "extremely spicy"
    )


def test_non_string_reason_is_replaced():
    assert normalize_verdict({"reason": None})["reason"] == ""
    assert normalize_verdict({"reason": {"a": 1}})["reason"] == ""


def test_input_is_not_mutated():
    raw = {"threat_level": "phishing"}
    normalize_verdict(raw)
    assert raw == {"threat_level": "phishing"}
