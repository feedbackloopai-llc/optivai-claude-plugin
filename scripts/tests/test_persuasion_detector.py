"""test_persuasion_detector.py - the pure-code persuasion-bombing detector (L0)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import persuasion_detector as pd  # noqa: E402


SEVEN_CLAUSES = {
    "show-work-not-persuade",
    "restate-facts-plainly",
    "no-rhetorical-escalation",
    "no-effusive-apology",
    "no-unprompted-avalanche",
    "confidence-forward",
    "pushback-re-examine",
}


def test_bijection_covers_all_seven_clauses_exactly():
    """Every signal maps to a clause, and all seven clauses are covered - no
    prose-only clause (unenforceable) and no signal without a clause."""
    assert set(pd.SIGNAL_CLAUSE.values()) == SEVEN_CLAUSES
    # each signal maps to exactly one clause (dict keys unique by construction)
    assert len(pd.SIGNAL_CLAUSE) == 7


def test_challenge_detection():
    assert pd.is_challenge("Are you sure? that doesn't align with the data")
    assert pd.is_challenge("check your work")
    assert pd.is_challenge("really?")
    assert not pd.is_challenge("thanks, please continue")
    assert not pd.is_challenge("")


def test_flattery_and_apology_fire_their_signal():
    r = pd.score_turn("You're absolutely right, sharp eye! I apologize for the confusion.")
    fired = {s["signal"] for s in r["signals"]}
    assert "apology_flattery_density" in fired
    assert r["score"] > 0.5


def test_doubling_down_fires_pushback_clause():
    r = pd.score_turn("After re-analyzing, I can confirm my conclusions remain valid and I stand by them.")
    got = {s["signal"]: s["clause"] for s in r["signals"]}
    assert "doubling_down" in got
    assert got["doubling_down"] == "pushback-re-examine"


def test_clean_grounded_hedged_answer_scores_low():
    msg = (
        "Page 4 shows revenue fell because the women's segment declined from 46% to 40%. "
        "I ran the numbers and it might be a mix shift, though I'm not certain - roughly a 6 point drop."
    )
    r = pd.score_turn(msg)
    assert r["score"] < 0.5, r
    assert pd.flag_line(r) is None


def test_avalanche_after_challenge_fires():
    prior = "Invest in brand A."
    huge = "Invest in brand A. " + ("Furthermore the market dynamics indicate " * 60)
    r = pd.score_turn(huge, prior_assistant=prior, was_challenged=True)
    fired = {s["signal"] for s in r["signals"]}
    assert "post_challenge_volume_ratio" in fired


def test_volume_ratio_does_not_fire_without_challenge():
    prior = "Short."
    huge = "Short. " + ("more text here " * 60)
    r = pd.score_turn(huge, prior_assistant=prior, was_challenged=False)
    fired = {s["signal"] for s in r["signals"]}
    assert "post_challenge_volume_ratio" not in fired


def test_escalation_delta_fires_under_challenge():
    prior = "The estimate is around forty percent based on the table."
    now = "This is clearly, obviously, undeniably, certainly and definitely correct without a doubt."
    r = pd.score_turn(now, prior_assistant=prior, was_challenged=True)
    fired = {s["signal"] for s in r["signals"]}
    assert "escalation_marker_delta" in fired


def test_missing_uncertainty_on_confident_unhedged_turn():
    msg = (
        "The answer is 42. This is the definitive result and the only correct interpretation "
        "of the data provided here, and there is no other reasonable reading of these figures "
        "whatsoever, so the conclusion is fixed and final for this analysis."
    )
    r = pd.score_turn(msg)
    fired = {s["signal"] for s in r["signals"]}
    assert "missing_uncertainty" in fired


def test_conclusion_without_derivation():
    msg = (
        "Obviously the strategy is correct and undeniably the best option available to the "
        "company right now, and it is certainly the strongest move the leadership team could "
        "possibly make at this point in the cycle."
    )
    r = pd.score_turn(msg)
    fired = {s["signal"] for s in r["signals"]}
    assert "conclusion_without_derivation" in fired


def test_score_is_max_over_signals_and_flag_threshold():
    r = pd.score_turn("You're absolutely right! sharp eye, great catch, spot on, brilliant observation.")
    assert r["score"] == max((s["value"] for s in r["signals"]), default=0.0)
    if r["score"] >= 0.5:
        assert pd.flag_line(r) is not None


# ── Turn-condition state bridge (Stop-hook producer -> capture consumer) ──────
def test_turn_condition_roundtrip_same_session(tmp_path):
    p = str(tmp_path / "state.json")
    pd.write_turn_condition("sess-1", 0.8, path=p)
    assert pd.read_turn_condition("sess-1", path=p) == 0.8


def test_turn_condition_different_session_ignored(tmp_path):
    p = str(tmp_path / "state.json")
    pd.write_turn_condition("sess-1", 0.8, path=p)
    assert pd.read_turn_condition("sess-2", path=p) == 0.0


def test_turn_condition_stale_ignored(tmp_path):
    p = str(tmp_path / "state.json")
    pd.write_turn_condition("sess-1", 0.8, path=p)
    assert pd.read_turn_condition("sess-1", path=p, ttl=0) == 0.0  # ttl=0 -> already stale


def test_turn_condition_missing_file_is_zero(tmp_path):
    assert pd.read_turn_condition("sess-1", path=str(tmp_path / "none.json")) == 0.0


def test_read_turn_condition_no_session_filter(tmp_path):
    # a read with no session_id still returns the recorded score (CLI captures)
    p = str(tmp_path / "state.json")
    pd.write_turn_condition("sess-1", 0.7, path=p)
    assert pd.read_turn_condition(None, path=p) == 0.7
