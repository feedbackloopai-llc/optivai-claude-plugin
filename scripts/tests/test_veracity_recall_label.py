"""test_veracity_recall_label.py - the recall-time veracity label (V1 point d).

When a discounted (persuasion-bombing-conditioned) atom is recalled, search output
surfaces a [LOW-VERACITY: produced under pushback] marker so downstream reasoning
and the human see the flag. Pure-logic tests of the flag + the rendered marker.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain as ob  # noqa: E402


def test_is_condition_discounted_dict():
    assert ob._is_condition_discounted({"condition_discounted": True}) is True
    assert ob._is_condition_discounted({"condition_discounted": False}) is False
    assert ob._is_condition_discounted({"other": 1}) is False


def test_is_condition_discounted_json_string():
    assert ob._is_condition_discounted('{"condition_discounted": true}') is True
    assert ob._is_condition_discounted('{"condition_discounted": false}') is False


def test_is_condition_discounted_none_and_malformed():
    assert ob._is_condition_discounted(None) is False
    assert ob._is_condition_discounted("not json") is False
    assert ob._is_condition_discounted(42) is False


def _result(**over):
    r = {
        "THOUGHT_ID": "t-abc12345",
        "RAW_TEXT": "an assessment",
        "SUMMARY": "an assessment",
        "SIMILARITY": 0.9,
        "CREATED_AT": "2026-07-08",
        "THOUGHT_TYPE": "decision",
        "STV": {"f": 1.0, "c": 0.5},
    }
    r.update(over)
    return r


def test_format_surfaces_veracity_label_when_discounted():
    out = ob._format_search_results([_result(CONDITION_DISCOUNTED=True)])
    assert "[LOW-VERACITY: produced under pushback]" in out


def test_format_no_veracity_label_when_clean():
    out = ob._format_search_results([_result(CONDITION_DISCOUNTED=False)])
    assert "LOW-VERACITY" not in out


def test_low_veracity_and_low_confidence_can_co_occur():
    out = ob._format_search_results(
        [_result(STV={"f": 1.0, "c": 0.2}, LOW_CONFIDENCE=True, CONDITION_DISCOUNTED=True)]
    )
    assert "[LOW-CONFIDENCE]" in out
    assert "[LOW-VERACITY: produced under pushback]" in out
