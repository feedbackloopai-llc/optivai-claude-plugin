"""test_veracity_capture_discount.py - the V1 confidence-discount at capture.

A pushback-produced assessment (persuasion-bombing tells in its text) must enter
memory with LOWER confidence, never higher. Pure-logic tests of the discount math
and the condition scorer; the DB write path is covered by the integration suite.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain as ob  # noqa: E402


def test_discount_lowers_confidence_under_condition():
    assert ob._discount_confidence(0.7, 1.0) < 0.7
    assert ob._discount_confidence(0.9, 0.6) < 0.9


def test_discount_scales_with_condition():
    low = ob._discount_confidence(0.8, 0.5)
    high = ob._discount_confidence(0.8, 1.0)
    assert high < low < 0.8  # stronger condition -> lower confidence


def test_discount_zero_condition_is_identity():
    # condition 0 -> r=1 -> weight unchanged -> confidence unchanged (within fp)
    assert abs(ob._discount_confidence(0.7, 0.0) - 0.7) < 1e-6


def test_discount_never_exceeds_input():
    for c in (0.2, 0.5, 0.7, 0.9, 0.99):
        for cond in (0.0, 0.3, 0.5, 0.8, 1.0):
            assert ob._discount_confidence(c, cond) <= c + 1e-9


def test_discount_numeric_matches_weight_scaling():
    # c=0.7 -> w=2.333; condition 1.0 -> r=0.4 -> w'=0.933 -> c'=0.4828
    got = ob._discount_confidence(0.7, 1.0)
    assert abs(got - 0.4828) < 0.01


def test_ceiling_caps_confident_self_stamp():
    """A confident self-stamp (high c) under a strong condition must be capped by
    the ceiling, not left near its stamped value."""
    # c=0.95 alone weight-discounts only to ~0.79; the ceiling at cond=1.0 is 0.50
    assert ob._discount_confidence(0.95, 1.0) <= 0.55
    # ceiling falls with condition: cond 0.5 -> 0.675
    assert ob._discount_confidence(0.95, 0.5) <= 0.68


def test_condition_score_flags_persuasion_bomb_text_high():
    bomb = (
        "After re-analyzing, I can confirm my conclusions remain valid. You are absolutely "
        "right - sharp eye! This is clearly and undeniably correct without a doubt."
    )
    assert ob._persuasion_condition_score(bomb) >= ob.CONDITION_DISCOUNT_THRESHOLD


def test_condition_score_clean_grounded_text_low():
    clean = (
        "Page 5 shows the women's share fell from 46% to 40% because of a mix shift. "
        "I might be wrong on the cause - roughly a 6 point drop; worth verifying against 2018."
    )
    assert ob._persuasion_condition_score(clean) < ob.CONDITION_DISCOUNT_THRESHOLD


def test_end_to_end_bomb_text_would_be_discounted_below_clean():
    """A persuasion-bomb assessment captured at nominal high confidence lands below
    a clean assessment captured at the same nominal confidence."""
    bomb = "I stand by this and can confirm it is clearly and undeniably the only correct answer."
    nominal_c = 0.9
    cond = ob._persuasion_condition_score(bomb)
    discounted = ob._discount_confidence(nominal_c, cond) if cond >= ob.CONDITION_DISCOUNT_THRESHOLD else nominal_c
    assert discounted < nominal_c
