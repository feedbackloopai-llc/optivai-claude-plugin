"""test_veracity_ranking.py - VL-6: the search-side veracity penalty.

A low-veracity (persuasion-bombing-conditioned) atom should DEMOTE in ranking, not
just carry a label. The penalty is MARGINAL by design: it must demote a marginally-
relevant low-veracity atom but MUST NOT suppress a highly-relevant flawed one (the
recall label is that atom's primary defense). These are pure-function tests of the
ranking helpers; the DB-backed search path is covered by the integration suite.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain as ob  # noqa: E402


def test_penalty_demotes_a_low_veracity_atom_vs_equal_clean():
    # two atoms, identical base relevance; the high-condition one ends up lower
    clean = ob._apply_veracity_penalty(0.50, 0.0)
    flawed = ob._apply_veracity_penalty(0.50, 1.0)
    assert flawed < clean


def test_relevant_flawed_atom_still_out_ranks_a_marginal_clean_one():
    """The honest-sizing proof: a HIGHLY-relevant flawed atom must still surface
    above a marginally-relevant clean atom - the penalty demotes, never suppresses."""
    relevant_flawed = ob._apply_veracity_penalty(0.85, 1.0)   # high relevance, fully bombed
    marginal_clean = ob._apply_veracity_penalty(0.40, 0.0)    # low relevance, clean
    assert relevant_flawed > marginal_clean


def test_degrade_to_today_when_condition_zero():
    # a clean or pre-V1 atom (condition 0) is ranked exactly as before
    assert ob._apply_veracity_penalty(0.5, 0.0) == 0.5
    assert ob._apply_veracity_penalty(0.9123, 0.0) == 0.9123


def test_penalty_scales_with_condition():
    low = ob._apply_veracity_penalty(0.8, 0.3)
    high = ob._apply_veracity_penalty(0.8, 0.9)
    assert high < low < 0.8


def test_penalty_exact_value():
    # penalty == VERACITY_PENALTY_COEFFICIENT * condition_score
    assert abs(ob._apply_veracity_penalty(0.7, 1.0) - (0.7 - ob.VERACITY_PENALTY_COEFFICIENT)) < 1e-9


def test_penalty_clamps_at_zero():
    assert ob._apply_veracity_penalty(0.05, 1.0) == 0.0  # 0.05 - 0.10 -> clamped to 0


def test_penalty_clamps_condition_and_handles_bad_input():
    assert ob._apply_veracity_penalty(0.8, 5.0) == ob._apply_veracity_penalty(0.8, 1.0)  # >1 clamps
    assert ob._apply_veracity_penalty(0.8, -1.0) == 0.8  # <0 clamps to 0 -> no penalty
    assert ob._apply_veracity_penalty(0.8, None) == 0.8  # bad input -> no penalty


def test_marginal_lever_is_smaller_than_relevance_gap():
    """Sizing sanity: the whole penalty (condition 1.0) is smaller than the vec-sim
    weight, so it cannot flip a large relevance gap."""
    assert ob.VERACITY_PENALTY_COEFFICIENT < 0.85  # vec_similarity weight in hybrid_score


def test_graph_scale_penalty_demotes_but_preserves_ordering():
    """Graph parity: graph scores are proximity*graph_weight (typically < 1). A high-
    condition graph atom demotes below an equal clean one, but a strong-proximity
    flawed atom still out-ranks a weak-proximity clean one (same marginal sizing)."""
    assert ob._apply_veracity_penalty(0.35, 1.0) < ob._apply_veracity_penalty(0.35, 0.0)
    assert ob._apply_veracity_penalty(0.60, 1.0) > ob._apply_veracity_penalty(0.30, 0.0)


def test_condition_score_extraction():
    assert ob._condition_score_of({"condition_score": 0.7}) == 0.7
    assert ob._condition_score_of('{"condition_score": 0.4}') == 0.4
    assert ob._condition_score_of({"condition_score": 5}) == 1.0   # clamp high
    assert ob._condition_score_of({"other": 1}) == 0.0             # absent
    assert ob._condition_score_of(None) == 0.0
    assert ob._condition_score_of("not json") == 0.0
