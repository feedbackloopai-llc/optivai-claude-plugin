"""test_detector.py — P2.1 unit tests for detect_candidates (pure function).

Test plan from docs/plans/2026-06-21-mayor-p2-reconciler.md §P2.1:
  1. crashed→candidate:      bead in recovery_blocked → StuckCandidate(kind="crashed")
  2. hung-past-threshold:    active, future not done, runtime > threshold → StuckCandidate(kind="hung")
  3. running-under-threshold: active, future not done, runtime < threshold → NOT a candidate
  4. healthy active workers:  future done → NOT a candidate (worker finished, Mayor hasn't reaped)
  5. mutates nothing:         active and recovery_blocked are unchanged after the call

Run: python3 -m pytest scripts/tests/test_detector.py -q
"""

from __future__ import annotations

import concurrent.futures
import sys
import time
from pathlib import Path
from typing import Set
from unittest.mock import MagicMock

import pytest

# Ensure scripts/ is on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# hooks/ needed for dispatch_gate imported by loop_runner
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import WorkerHandle
from reconciler import StuckCandidate, detect_candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _done_future(result=None) -> concurrent.futures.Future:
    """Return a Future that is already done."""
    f: concurrent.futures.Future = concurrent.futures.Future()
    f.set_result(result)
    return f


def _pending_future() -> concurrent.futures.Future:
    """Return a Future that is NOT done (pending)."""
    # Don't set a result — it stays pending.
    return concurrent.futures.Future()


def _handle(bead_id: str, started_at: float, done: bool = False, model: str = "sonnet") -> WorkerHandle:
    future = _done_future() if done else _pending_future()
    return WorkerHandle(bead_id=bead_id, future=future, model=model, started_at=started_at)


# ---------------------------------------------------------------------------
# Test 1 — crashed bead produces a candidate
# ---------------------------------------------------------------------------

class TestCrashedProducesCandidate:
    def test_crashed_in_recovery_blocked(self) -> None:
        """A bead_id in recovery_blocked → StuckCandidate with kind='crashed'."""
        recovery_blocked: Set[str] = {"fblai-crash1"}
        active = {}
        now = 1000.0
        threshold = 1800.0

        candidates = detect_candidates(active, recovery_blocked, now, threshold)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.bead_id == "fblai-crash1"
        assert c.kind == "crashed"

    def test_multiple_crashed_all_become_candidates(self) -> None:
        """Multiple crashed beads all become candidates."""
        recovery_blocked: Set[str] = {"fblai-crash1", "fblai-crash2", "fblai-crash3"}
        active = {}
        now = 5000.0
        threshold = 1800.0

        candidates = detect_candidates(active, recovery_blocked, now, threshold)
        candidate_ids = {c.bead_id for c in candidates}

        assert candidate_ids == recovery_blocked
        assert all(c.kind == "crashed" for c in candidates)

    def test_crashed_candidate_runtime_is_zero(self) -> None:
        """Crashed candidates report runtime_s=0.0 (no handle to measure from)."""
        recovery_blocked: Set[str] = {"fblai-crash-rt"}
        candidates = detect_candidates({}, recovery_blocked, now=9999.0, stuck_threshold_s=1800.0)
        assert candidates[0].runtime_s == 0.0


# ---------------------------------------------------------------------------
# Test 2 — hung bead (past threshold) produces a candidate
# ---------------------------------------------------------------------------

class TestHungPastThreshold:
    def test_hung_past_threshold_is_candidate(self) -> None:
        """Active bead whose future is pending and runtime > threshold → hung candidate."""
        now = 5000.0
        threshold = 1800.0
        started_at = now - 2000.0  # 2000s ago > 1800s threshold

        active = {"fblai-hung1": _handle("fblai-hung1", started_at=started_at, done=False)}
        candidates = detect_candidates(active, set(), now, threshold)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.bead_id == "fblai-hung1"
        assert c.kind == "hung"
        assert abs(c.runtime_s - 2000.0) < 0.01

    def test_hung_candidate_captures_model(self) -> None:
        """The model field is populated from the WorkerHandle."""
        now = 5000.0
        active = {"fblai-model": _handle("fblai-model", started_at=now - 2000.0, done=False, model="opus")}
        candidates = detect_candidates(active, set(), now, stuck_threshold_s=1800.0)

        assert candidates[0].model == "opus"


# ---------------------------------------------------------------------------
# Test 3 — running under threshold is NOT a candidate
# ---------------------------------------------------------------------------

class TestRunningUnderThreshold:
    def test_under_threshold_not_a_candidate(self) -> None:
        """Pending future but runtime < threshold → not a candidate."""
        now = 5000.0
        threshold = 1800.0
        started_at = now - 100.0  # only 100s ago

        active = {"fblai-young": _handle("fblai-young", started_at=started_at, done=False)}
        candidates = detect_candidates(active, set(), now, threshold)

        assert candidates == []

    def test_exactly_at_threshold_not_a_candidate(self) -> None:
        """runtime_s == stuck_threshold_s → not a candidate (strictly greater required)."""
        now = 5000.0
        threshold = 1800.0
        started_at = now - threshold  # exactly at threshold

        active = {"fblai-exact": _handle("fblai-exact", started_at=started_at, done=False)}
        candidates = detect_candidates(active, set(), now, threshold)

        # runtime_s == threshold, NOT > threshold → not a candidate
        assert candidates == []


# ---------------------------------------------------------------------------
# Test 4 — healthy active workers (future done) are NOT candidates
# ---------------------------------------------------------------------------

class TestHealthyWorkerNotCandidate:
    def test_done_future_not_a_candidate(self) -> None:
        """A bead whose future is already done is not a hung candidate — Mayor will reap it."""
        now = 5000.0
        started_at = now - 9999.0  # very old but future is done

        active = {"fblai-done": _handle("fblai-done", started_at=started_at, done=True)}
        candidates = detect_candidates(active, set(), now, stuck_threshold_s=1800.0)

        assert candidates == []

    def test_mix_of_done_and_pending(self) -> None:
        """Only the pending-past-threshold bead becomes a candidate."""
        now = 5000.0
        threshold = 1800.0

        active = {
            "fblai-done2":    _handle("fblai-done2",    started_at=now - 9000.0, done=True),
            "fblai-pending2": _handle("fblai-pending2", started_at=now - 2000.0, done=False),
            "fblai-young2":   _handle("fblai-young2",   started_at=now - 50.0,   done=False),
        }
        candidates = detect_candidates(active, set(), now, threshold)

        assert len(candidates) == 1
        assert candidates[0].bead_id == "fblai-pending2"
        assert candidates[0].kind == "hung"


# ---------------------------------------------------------------------------
# Test 5 — mutates nothing
# ---------------------------------------------------------------------------

class TestMutatesNothing:
    def test_active_unchanged_after_detect(self) -> None:
        """detect_candidates must not mutate the active dict."""
        now = 5000.0
        active = {
            "fblai-m1": _handle("fblai-m1", started_at=now - 2000.0, done=False),
            "fblai-m2": _handle("fblai-m2", started_at=now - 50.0,   done=False),
        }
        active_keys_before = set(active.keys())

        detect_candidates(active, set(), now, stuck_threshold_s=1800.0)

        assert set(active.keys()) == active_keys_before

    def test_recovery_blocked_unchanged_after_detect(self) -> None:
        """detect_candidates must not mutate recovery_blocked."""
        recovery_blocked: Set[str] = {"fblai-rb1", "fblai-rb2"}
        rb_before = set(recovery_blocked)

        detect_candidates({}, recovery_blocked, now=9999.0, stuck_threshold_s=1800.0)

        assert recovery_blocked == rb_before

    def test_both_sets_unchanged_with_candidates(self) -> None:
        """Both active and recovery_blocked are unchanged even when candidates are found."""
        now = 5000.0
        recovery_blocked: Set[str] = {"fblai-rb"}
        active = {"fblai-hung": _handle("fblai-hung", started_at=now - 2000.0, done=False)}

        rb_before = set(recovery_blocked)
        active_keys_before = set(active.keys())

        candidates = detect_candidates(active, recovery_blocked, now, stuck_threshold_s=1800.0)

        assert len(candidates) == 2
        assert recovery_blocked == rb_before
        assert set(active.keys()) == active_keys_before
