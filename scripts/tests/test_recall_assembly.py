#!/usr/bin/env python3
"""T2 / fblai-am3u0 — recall_assembly.py (pure token-budget recall assembly).

Concrete-contract tests against the already-gated T1 design at
``docs/plans/2026-07-02-recall-assembly-t1-design.md``. Every test below
maps to a named invariant or a named test from the design's §6.1 gate /
the parent plan (``2026-06-18-native-recall-compression.md`` T2 step 1):

- The 9 named T2 tests (parent plan, T2 "Step 1").
- Budget invariants B1-B6 (§2.6).
- CCR invariants R2, R4, R5 (§4.4).
- Fidelity invariants F1-F4 (§5).

This module is PURE (no I/O, no DB) — every test here runs unconditionally,
no DATABASE_URL / --integration marker required.

Run:
    python3 -m pytest scripts/tests/test_recall_assembly.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recall_assembly  # noqa: E402


# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _id(i: int) -> str:
    """A realistic-looking brain-{epoch}-{8hex} thought id."""
    return f"brain-{1720000000 + i}-{i:08x}"


def _lorem(n: int) -> str:
    """Deterministic filler text whose length varies with n."""
    sentence = f"Sentence number {n} provides filler text for budget testing. "
    return sentence * (1 + (n % 5))


def _make_atom(thought_id: str, summary=None, raw_text=None,
                created_at: str = "2026-07-01T12:00:00",
                thought_type: str = "insight", **extra) -> dict:
    atom = {
        "THOUGHT_ID": thought_id,
        "CREATED_AT": created_at,
        "THOUGHT_TYPE": thought_type,
    }
    if summary is not None:
        atom["SUMMARY"] = summary
    if raw_text is not None:
        atom["RAW_TEXT"] = raw_text
    atom.update(extra)
    return atom


# ─── The 9 named T2 tests (parent plan, T2 Step 1) ────────────────────────────


def test_count_tokens_fallback_without_tiktoken(monkeypatch):
    """monkeypatch import -> len//4 fallback."""
    monkeypatch.setattr(recall_assembly, "_encoder", None)
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    text = "hello world, this is a fallback verification test"
    result = recall_assembly.count_tokens(text)
    assert result == max(1, len(text) // recall_assembly.CHARS_PER_TOKEN_ESTIMATE)


def test_summarize_to_breaks_on_sentence_boundary():
    """Plan-pinned example: 'A. B. C.' cap 4 -> 'A.…' not 'A. B'."""
    assert recall_assembly.summarize_to("A. B. C.", 4) == "A.…"
    assert recall_assembly.summarize_to("A. B. C.", 4) == "A." + recall_assembly.ELLIPSIS


def test_summarize_to_hard_cut_when_no_boundary():
    text = "ABCDEFGHIJKLMNOP"  # no boundary chars, no newline
    result = recall_assembly.summarize_to(text, 5)
    assert result == "ABCD" + recall_assembly.ELLIPSIS
    assert len(result) <= 5


def test_project_fields_keeps_only_listed():
    record = {"THOUGHT_ID": "brain-1-abcd1234", "SUMMARY": "hi", "EXTRA": "drop me"}
    result = recall_assembly.project_fields(record, keep=["SUMMARY"])
    assert result == {"SUMMARY": "hi", "THOUGHT_ID": "brain-1-abcd1234"}
    assert "EXTRA" not in result


def test_project_fields_collapses_nested_list_to_count():
    record = {"THOUGHT_ID": "id1", "links": [1, 2, 3, 4, 5]}
    result = recall_assembly.project_fields(record, keep=["THOUGHT_ID"])
    assert result == {"THOUGHT_ID": "id1", "links_count": 5}


def test_assemble_recall_respects_token_budget():
    """10 fat atoms, budget 200 -> included < 10, total <= 200."""
    atoms = [_make_atom(_id(i), summary="X" * 500) for i in range(10)]
    result = recall_assembly.assemble_recall(atoms, token_budget=200)
    assert result["included"] < 10
    assert result["token_count"] <= 200


def test_assemble_recall_never_drops_top_ranked():
    """Single atom > budget -> summarized, included == 1."""
    atoms = [_make_atom(_id(0), summary="Y" * 5000)]
    result = recall_assembly.assemble_recall(atoms, token_budget=50)
    assert result["included"] == 1
    assert result["annotations"][_id(0)]["summarized"] is True
    assert result["token_count"] <= 50


def test_assemble_recall_appends_expand_hint():
    """rendered ends with the --inspect hint."""
    atoms = [_make_atom(_id(0), summary="short summary")]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert "--inspect" in result["rendered"]
    assert result["rendered"].endswith(recall_assembly.EXPAND_HINT)


def test_assemble_recall_empty_atoms_returns_empty():
    """No crash, included 0."""
    result = recall_assembly.assemble_recall([], token_budget=600)
    assert result["included"] == 0
    assert result["dropped"] == 0
    assert result["rendered"] == ""
    assert result["lines"] == []


# ─── B1-B6 budget invariants (§2.6) ────────────────────────────────────────────


@pytest.mark.parametrize("budget", [50, 100, 200, 600, 1000])
def test_b1_never_exceeds_budget(budget):
    for n in (0, 1, 3, 10, 25):
        atoms = [_make_atom(_id(i), summary=_lorem(i)) for i in range(n)]
        result = recall_assembly.assemble_recall(atoms, token_budget=budget)
        assert result["token_count"] <= budget, (
            f"B1 violated: n={n}, budget={budget}, token_count={result['token_count']}"
        )


def test_b2_fallback_fail_open_and_budget_holds(monkeypatch):
    """Force the tiktoken path to fail; fallback formula holds AND B1 still holds."""
    monkeypatch.setattr(recall_assembly, "_encoder", None)
    monkeypatch.setitem(sys.modules, "tiktoken", None)

    text = "some sample text used to verify the fail-open fallback formula"
    assert recall_assembly.count_tokens(text) == max(
        1, len(text) // recall_assembly.CHARS_PER_TOKEN_ESTIMATE
    )

    atoms = [_make_atom(_id(i), summary=_lorem(i)) for i in range(8)]
    result = recall_assembly.assemble_recall(atoms, token_budget=150)
    assert result["token_count"] <= 150


def test_b3_never_drop_higher_for_lower_fat_then_thin():
    """Fat atom (rank 2) can't fit even at the floor; thin atom (rank 3) still
    gets a shot afterward — the scan does not stop at a drop, and no
    higher-relevance atom is ever displaced by a lower one."""
    top = _make_atom(_id(0), summary="short top summary that fits easily")
    fat = _make_atom(_id(1), summary="F" * 4000)
    thin = _make_atom(_id(2), summary="tiny")
    atoms = [top, fat, thin]

    result = recall_assembly.assemble_recall(atoms, token_budget=90)

    assert _id(1) in result["dropped_ids"], "fat atom should be budget-dropped"
    included_order = list(result["annotations"].keys())
    assert _id(0) in included_order
    assert _id(2) in included_order
    assert _id(1) not in included_order
    # Order preserved: top precedes thin in the included sequence.
    assert included_order.index(_id(0)) < included_order.index(_id(2))


def test_b4_top_atom_never_dropped_degenerate_budget():
    """Single atom larger than budget -> included == 1, summarized, B1 holds."""
    atoms = [_make_atom(_id(0), summary="Z" * 8000)]
    result = recall_assembly.assemble_recall(atoms, token_budget=40)
    assert result["included"] == 1
    assert result["dropped"] == 0
    assert result["annotations"][_id(0)]["summarized"] is True
    assert result["token_count"] <= 40


def test_b5_order_preservation():
    """The sequence of THOUGHT_IDs (as short-ids in lines) is a subsequence
    of the input sequence."""
    atoms = [_make_atom(_id(i), summary=_lorem(i)) for i in range(8)]
    input_order = [a["THOUGHT_ID"] for a in atoms]

    result = recall_assembly.assemble_recall(atoms, token_budget=150)
    included_order = list(result["annotations"].keys())

    # Subsequence check: each included id must appear, in order, within
    # the input sequence.
    it = iter(input_order)
    assert all(x in it for x in included_order), (
        f"included order {included_order} is not a subsequence of "
        f"input order {input_order}"
    )

    # Each rendered line actually carries its atom's short-id, in the same
    # relative order as the annotations.
    for full_id, line in zip(included_order, result["lines"]):
        assert full_id[-recall_assembly.SHORT_ID_CHARS:] in line


def test_b6_purity_and_no_mutation():
    """Equal inputs -> equal outputs; atoms list and its dicts are not mutated."""
    atoms = [_make_atom(_id(i), summary=_lorem(i)) for i in range(5)]
    atoms_snapshot = copy.deepcopy(atoms)

    result1 = recall_assembly.assemble_recall(atoms, token_budget=150)
    result2 = recall_assembly.assemble_recall(atoms, token_budget=150)

    assert result1 == result2
    assert atoms == atoms_snapshot, "assemble_recall must not mutate its input"


# ─── R2, R4, R5 (§4.4) ──────────────────────────────────────────────────────────


def test_r2_project_fields_keeps_id_even_when_absent_from_keep():
    record = {"THOUGHT_ID": "brain-1-deadbeef", "SUMMARY": "s", "OTHER": "x"}
    result = recall_assembly.project_fields(record, keep=["SUMMARY"])
    assert result["THOUGHT_ID"] == "brain-1-deadbeef"

    record2 = {"thought_id": "brain-1-deadbeef", "SUMMARY": "s"}
    result2 = recall_assembly.project_fields(record2, keep=["SUMMARY"])
    assert result2["thought_id"] == "brain-1-deadbeef"


def test_r4_rendered_ends_with_expand_hint_when_included():
    atoms = [_make_atom(_id(0), summary="hello")]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert result["included"] >= 1
    assert result["rendered"].endswith(recall_assembly.EXPAND_HINT)


def test_r5_summarization_marked():
    atoms = [_make_atom(_id(0), summary="Z" * 5000)]
    result = recall_assembly.assemble_recall(atoms, token_budget=60)
    assert result["annotations"][_id(0)]["summarized"] is True


# ─── F1-F4 fidelity invariants (§5) ────────────────────────────────────────────


def test_f1_disputed_flag_rendered():
    atoms = [_make_atom(_id(0), summary="disputed atom summary",
                         DISPUTED={"by": ["x"], "types": ["y"]})]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert "[disputed]" in result["lines"][0]


def test_f2_superseded_flag_rendered():
    atoms = [_make_atom(_id(0), summary="superseded atom",
                         SUPERSEDED_BY=["brain-1-aaaaaaaa"])]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert "[superseded]" in result["lines"][0]


def test_f3_low_confidence_flag_rendered():
    atoms = [_make_atom(_id(0), summary="low conf atom", LOW_CONFIDENCE=True)]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert "[low-conf]" in result["lines"][0]


def test_f4_no_embedding_egress():
    atoms = [_make_atom(_id(0), summary="embedding test atom",
                         _EMBEDDING=[0.111, 0.222, 0.333])]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    assert "_EMBEDDING" not in result["rendered"]
    assert "0.111" not in result["rendered"]
    for line in result["lines"]:
        assert "_EMBEDDING" not in line
    assert all("_EMBEDDING" not in k for k in result["annotations"])
    serialized = json.dumps(result, default=str)
    assert "_EMBEDDING" not in serialized


# ─── Additional coverage: flag ordering + malformed-atom handling ─────────────


def test_flags_render_in_order_low_conf_disputed_superseded():
    atoms = [_make_atom(
        _id(0), summary="triple-flagged atom",
        LOW_CONFIDENCE=True,
        DISPUTED={"by": ["x"]},
        SUPERSEDED_BY=["brain-1-bbbbbbbb"],
    )]
    result = recall_assembly.assemble_recall(atoms, token_budget=600)
    line = result["lines"][0]
    low_idx = line.index("[low-conf]")
    disputed_idx = line.index("[disputed]")
    superseded_idx = line.index("[superseded]")
    assert low_idx < disputed_idx < superseded_idx


def test_atoms_missing_thought_id_are_skipped_not_in_dropped_ids():
    good = _make_atom(_id(0), summary="a fine atom")
    malformed = {"SUMMARY": "no id here"}
    non_dict = "not-even-a-dict"
    result = recall_assembly.assemble_recall([good, malformed, non_dict], token_budget=600)
    assert result["included"] == 1
    assert result["dropped"] == 2
    assert result["dropped_ids"] == []  # id-less atoms are never in dropped_ids


def test_assemble_recall_invalid_input_types_fail_open():
    assert recall_assembly.assemble_recall(None, token_budget=600)["included"] == 0
    assert recall_assembly.assemble_recall("not-a-list", token_budget=600)["included"] == 0
    assert recall_assembly.assemble_recall({}, token_budget=600)["included"] == 0


def test_assemble_recall_non_positive_budget_returns_empty():
    atoms = [_make_atom(_id(0), summary="anything")]
    result = recall_assembly.assemble_recall(atoms, token_budget=0)
    assert result["included"] == 0
    assert result["rendered"] == ""
    result_neg = recall_assembly.assemble_recall(atoms, token_budget=-10)
    assert result_neg["included"] == 0


def test_summarize_to_unchanged_when_within_max_chars():
    assert recall_assembly.summarize_to("short", 100) == "short"


def test_summarize_to_max_chars_below_two():
    assert recall_assembly.summarize_to("hello world", 1) == "h"
    assert recall_assembly.summarize_to("hello world", 0) == ""


def test_project_fields_does_not_mutate_input():
    record = {"THOUGHT_ID": "id1", "links": [1, 2, 3]}
    record_snapshot = copy.deepcopy(record)
    recall_assembly.project_fields(record, keep=["THOUGHT_ID"])
    assert record == record_snapshot


# ─── Review folds (Gate-2: MINOR-1 / MINOR-2 / NIT-3 / NIT-4) ─────────────────


def test_negative_budget_returns_empty_not_a_violation():
    # NIT-3: §2.5 - token_budget <= 0 -> empty result (included 0), never a crash
    # and never a `token_count <= budget` claim on a negative budget.
    atoms = [{"THOUGHT_ID": _id(1), "SUMMARY": "hello", "CREATED_AT": "2026-07-02", "THOUGHT_TYPE": "pattern"}]
    for b in (-10, -1, 0):
        out = recall_assembly.assemble_recall(atoms, b)
        assert out["included"] == 0
        assert out["lines"] == [] and out["rendered"] == ""


def test_summarize_to_max_chars_two_and_below_boundary():
    # NIT-4: the max_chars == 2 boundary (the >= 2 guarantee) + the < 2 slice path.
    assert len(recall_assembly.summarize_to("abcdef", 2)) <= 2
    assert recall_assembly.summarize_to("abcdef", 1) == "a"   # < 2 -> text[:max_chars]
    assert recall_assembly.summarize_to("abcdef", 0) == ""


def test_non_string_summary_and_dup_count_fail_open():
    # MINOR-2: a non-string SUMMARY / non-numeric NEAR_DUPLICATE_COUNT (outside the
    # search()-shaped contract) must NOT raise inside assembly - coerced (fail-open, §5).
    atoms = [{
        "THOUGHT_ID": _id(2), "CREATED_AT": "2026-07-02", "THOUGHT_TYPE": "pattern",
        "SUMMARY": ["a", "list"], "NEAR_DUPLICATE_COUNT": "3",
    }]
    out = recall_assembly.assemble_recall(atoms, 600)   # must not raise
    assert out["included"] == 1


def test_sub_hint_budget_returns_empty_result():
    # MINOR-1 (documented §2.3 reading): a budget too small for even the hint ->
    # empty result; included 0, token_count 0 (no budget violation).
    atoms = [{"THOUGHT_ID": _id(3), "SUMMARY": "x" * 400, "CREATED_AT": "2026-07-02", "THOUGHT_TYPE": "pattern"}]
    out = recall_assembly.assemble_recall(atoms, 3)     # far below the hint cost
    assert out["included"] == 0 and out["token_count"] == 0
