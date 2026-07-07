"""test_refute.py - unit tests for the independent adversarial refuter.

Covers the pure logic (prompt building, schema parsing + the anti-sycophancy property,
model selection, rendering) without any live Ollama call.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import refute  # noqa: E402


def test_build_user_prompt_includes_claim_context_confidence():
    p = refute.build_user_prompt("invest in brand X", "share fell 46->40%", "high")
    assert "invest in brand X" in p
    assert "share fell 46->40%" in p
    assert "high" in p


def test_build_user_prompt_claim_only_when_no_extras():
    p = refute.build_user_prompt("some claim", None, None)
    assert "some claim" in p
    assert "GROUNDING" not in p


def test_parse_valid_refutation():
    raw = (
        '{"verdict":"gap","strongestCounterCase":"the growth is unproven vs peers",'
        '"flaws":[{"claim":"no comparative data","severity":"high"}],'
        '"confidenceAdjustment":-0.5}'
    )
    r = refute.parse_refutation(raw)
    assert r["verdict"] == "gap"
    assert r["strongestCounterCase"].startswith("the growth")
    assert r["flaws"][0]["severity"] == "high"
    assert r["confidenceAdjustment"] == -0.5


def test_parse_rejects_empty_counter_case():
    """The anti-sycophancy property: an empty blessing is a failure, not a pass."""
    raw = '{"verdict":"holds","strongestCounterCase":"","flaws":[],"confidenceAdjustment":0}'
    with pytest.raises(refute.RefuterError):
        refute.parse_refutation(raw)


def test_parse_rejects_trivial_counter_case():
    raw = '{"verdict":"holds","strongestCounterCase":"looks good","flaws":[],"confidenceAdjustment":0}'
    with pytest.raises(refute.RefuterError):
        refute.parse_refutation(raw)


def test_parse_rejects_malformed_json():
    with pytest.raises(refute.RefuterError):
        refute.parse_refutation("not json at all")


def test_parse_rejects_bad_verdict():
    raw = '{"verdict":"maybe","strongestCounterCase":"a real substantive counter-case here","flaws":[]}'
    with pytest.raises(refute.RefuterError):
        refute.parse_refutation(raw)


def test_parse_normalizes_flaws_and_clamps_adjustment():
    raw = (
        '{"verdict":"broken","strongestCounterCase":"the premise is false because ...",'
        '"flaws":[{"claim":"bad","severity":"CRITICAL"},{"nope":"x"}],'
        '"confidenceAdjustment":-9}'
    )
    r = refute.parse_refutation(raw)
    # unknown severity -> medium; malformed flaw dropped
    assert r["flaws"] == [{"claim": "bad", "severity": "medium"}]
    # out-of-range adjustment clamped to [-1, 0]
    assert r["confidenceAdjustment"] == -1.0


def test_pick_model_prefers_explicit_when_available():
    assert refute.pick_model("mistral:latest", ["llama3.2:latest", "mistral:latest"]) == "mistral:latest"


def test_pick_model_falls_back_to_preference_order():
    # explicit not present -> first of DEFAULT_MODEL_PREFERENCE that is available
    got = refute.pick_model("nonexistent:latest", ["gpt-oss:20b", "llama3.1:latest"])
    assert got == "llama3.1:latest"  # earlier in preference list than gpt-oss:20b


def test_pick_model_none_when_nothing_available():
    assert refute.pick_model(None, []) is None


def test_render_human_shows_verdict_countercase_and_reexamine_nudge():
    r = {
        "verdict": "gap",
        "strongestCounterCase": "unproven vs peers",
        "flaws": [{"claim": "no comparative data", "severity": "high"}],
        "confidenceAdjustment": -0.5,
    }
    out = refute.render_human(r, "mistral:latest")
    assert "GAP" in out
    assert "unproven vs peers" in out
    assert "Re-examine" in out  # clause-7 nudge fires on non-holds / negative adjustment
