"""test_nal_independence.py - the evidence-independence guard on NAL revision.

The bug: nal_revise pools evidential weight, so folding the SAME claim repeatedly
manufactures confidence (repetition -> high c) - the persuasion-bombing signature.
The guard: dependent observations (same session / direct derivation) do NOT
accumulate. Independent evidence still does. These are pure-logic tests; the
DB-backed dependency lookup (_atoms_dependent) is exercised by the integration
suite against a live pgvector DB.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


def test_dependent_revision_never_manufactures_confidence():
    """The money test: N restatements of one claim must NOT raise confidence.

    Without the guard, 10 folds of c=0.30 climb toward ~0.81 (Fable's worked case).
    With nal_revise_dependent, confidence stays at one-observation level.
    """
    f, c = 0.9, 0.30
    for _ in range(9):  # 10 total "observations" of the same claim
        f, c = open_brain.nal_revise_dependent(f, c, 0.9, 0.30)
    assert c <= 0.30 + 1e-9, f"dependent repetition manufactured confidence: c={c}"


def test_independent_revision_still_accumulates():
    """Contrast: genuinely independent evidence must still raise confidence."""
    f, c = 0.9, 0.30
    for _ in range(9):
        f, c = open_brain.nal_revise(f, c, 0.9, 0.30)
    assert c > 0.60, f"independent evidence failed to accumulate: c={c}"


def test_nal_revise_dependent_keeps_more_confident_belief_unchanged():
    # higher-confidence premise wins, unchanged (no accumulation)
    assert open_brain.nal_revise_dependent(0.8, 0.70, 0.2, 0.40) == (0.8, 0.70)
    assert open_brain.nal_revise_dependent(0.2, 0.40, 0.8, 0.70) == (0.8, 0.70)


def test_nal_revise_dependent_confidence_is_the_max_not_the_sum():
    _, c = open_brain.nal_revise_dependent(0.9, 0.55, 0.9, 0.55)
    assert abs(c - 0.55) < 1e-9  # equal premises -> unchanged, never 0.60+


def test_dependency_same_session_is_dependent():
    assert open_brain._dependency_from_signals("sess-1", "sess-1", False) is True


def test_dependency_different_session_no_derivation_is_independent():
    assert open_brain._dependency_from_signals("sess-1", "sess-2", False) is False


def test_dependency_direct_derivation_is_dependent_across_sessions():
    # one derives from the other -> dependent even in different sessions
    assert open_brain._dependency_from_signals("sess-1", "sess-2", True) is True


def test_dependency_missing_sessions_is_independent():
    # no session info and no derivation -> treat as independent (accumulate)
    assert open_brain._dependency_from_signals(None, None, False) is False
    assert open_brain._dependency_from_signals(None, "sess-1", False) is False


def test_atoms_dependent_fails_safe_to_dependent_on_db_error():
    """A provenance-lookup failure must never let confidence be manufactured."""
    class _BoomConn:
        def cursor(self):
            raise RuntimeError("db down")

    assert open_brain._atoms_dependent(_BoomConn(), "a", "b", "u") is True


# ── Transitive derivation-ancestry guard (multi-hop chains + shared roots) ────
class _FakeCursor:
    def __init__(self, atoms):
        self.atoms = atoms
        self._result = None

    def execute(self, sql, params):
        if "ANY(%s)" in sql:  # batch session/parent lookup
            ids = params[0]
            self._result = [
                (i, self.atoms[i]["session"], self.atoms[i]["parent"])
                for i in ids
                if i in self.atoms
            ]
        else:  # single-parent ancestry walk: WHERE thought_id = %s
            a = self.atoms.get(params[0])
            self._result = [(a["parent"],)] if a else []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, atoms):
        self.atoms = atoms

    def cursor(self):
        return _FakeCursor(self.atoms)


def test_derivation_ancestors_walks_the_chain():
    # A <- B <- C  (C derives from B derives from A)
    atoms = {
        "A": {"session": "s1", "parent": None},
        "B": {"session": "s2", "parent": "A"},
        "C": {"session": "s3", "parent": "B"},
    }
    assert open_brain._derivation_ancestors(_FakeConn(atoms), "C", "u") == {"A", "B"}


def test_atoms_dependent_transitive_across_sessions():
    """C derives (transitively) from A in a different session - still dependent."""
    atoms = {
        "A": {"session": "s1", "parent": None},
        "B": {"session": "s2", "parent": "A"},
        "C": {"session": "s3", "parent": "B"},
    }
    assert open_brain._atoms_dependent(_FakeConn(atoms), "A", "C", "u") is True


def test_atoms_dependent_shared_root_is_dependent():
    """B and C both descend from A (siblings) - same evidence, different paths."""
    atoms = {
        "A": {"session": "s1", "parent": None},
        "B": {"session": "s2", "parent": "A"},
        "C": {"session": "s3", "parent": "A"},
    }
    assert open_brain._atoms_dependent(_FakeConn(atoms), "B", "C", "u") is True


def test_atoms_dependent_truly_independent_is_independent():
    """No shared lineage and different sessions -> independent (may accumulate)."""
    atoms = {
        "A": {"session": "s1", "parent": None},
        "B": {"session": "s2", "parent": None},
    }
    assert open_brain._atoms_dependent(_FakeConn(atoms), "A", "B", "u") is False


def test_derivation_ancestors_cycle_guard_terminates():
    # A <-> B cycle must not loop forever
    atoms = {
        "A": {"session": "s1", "parent": "B"},
        "B": {"session": "s2", "parent": "A"},
    }
    anc = open_brain._derivation_ancestors(_FakeConn(atoms), "A", "u")
    assert anc == {"B", "A"} or anc == {"B"}  # terminates; exact set depends on stop point
