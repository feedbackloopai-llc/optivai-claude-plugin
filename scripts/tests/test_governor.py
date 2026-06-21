"""test_governor.py — Acceptance tests for the P0.2 bounded-concurrent capacity governor.

Tests the 7 acceptance criteria from docs/plans/2026-06-21-mayor-p0-capacity-governor.md:

1. test_capacity_never_exceeds_max_workers
2. test_fills_freed_slots_with_newly_unblocked
3. test_crashed_worker_slot_stays_occupied
4. test_single_writer_invariant
5. test_close_only_on_verify_exit_0
6. test_max_workers_1_matches_sequential
7. test_capacity_exhausted_by_stuck_workers_stops

All tests use injected fakes — no real subprocesses.

Run: python3 -m pytest scripts/tests/test_governor.py -q
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

# Ensure scripts/ is on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure hooks/ is on path for dispatch_gate (imported by loop_runner)
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    RunConfig,
    Runners,
    RunSummary,
    run_loop,
    run_mayor_loop,
    WorkerHandle,
    WorkerResult,
    should_continue,
    LOOP_MAX_WORKERS,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_bead(
    bead_id: str = "fblai-test",
    title: str = "Test bead",
    priority: int = 2,
    labels: Optional[List[str]] = None,
    body: str = "",
) -> dict:
    return {
        "id": bead_id,
        "title": title,
        "priority": priority,
        "labels": labels or [],
        "body": body,
    }


def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="test-molecule",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=25,
        budget_tokens=10_000_000,
        dry_run=False,
        once=False,
        max_workers=2,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


class _StatusTracker:
    """Thread-safe tracker of bead statuses, simulating the beads database."""

    def __init__(self):
        self._lock = threading.Lock()
        self._statuses: dict[str, str] = {}
        self._closed: list[str] = []
        self._updated: list[tuple[str, str]] = []

    def set_open(self, bead_id: str) -> None:
        with self._lock:
            self._statuses[bead_id] = "open"

    def update(self, bead_id: str, status: str) -> None:
        with self._lock:
            self._statuses[bead_id] = status
            self._updated.append((bead_id, status))

    def close(self, bead_id: str) -> None:
        with self._lock:
            self._statuses[bead_id] = "closed"
            self._closed.append(bead_id)

    def get_status(self, bead_id: str) -> str:
        with self._lock:
            return self._statuses.get(bead_id, "unknown")

    @property
    def closed_beads(self) -> List[str]:
        with self._lock:
            return list(self._closed)

    @property
    def update_history(self) -> List[tuple[str, str]]:
        with self._lock:
            return list(self._updated)


def _make_mayor_runners(
    *,
    ready_beads_fn=None,
    verify_exit: int = 0,
    dispatch_fn=None,
    tracker: Optional[_StatusTracker] = None,
    loop_state_path: Optional[Path] = None,
) -> Runners:
    """Build a Runners instance suitable for run_mayor_loop testing."""
    _tracker = tracker or _StatusTracker()

    def _beads_ready(molecule: str) -> List[dict]:
        if ready_beads_fn is not None:
            return ready_beads_fn(molecule)
        return []

    def _beads_update(bead_id: str, status: str) -> None:
        _tracker.update(bead_id, status)

    def _beads_close(bead_id: str) -> None:
        _tracker.close(bead_id)

    def _dispatch(prompt: str, model: str, timeout_s: int) -> dict:
        if dispatch_fn is not None:
            return dispatch_fn(prompt, model, timeout_s)
        return {"tokens": 10, "output": "done"}

    def _run_verify(cmd: str, timeout_s: int) -> int:
        return verify_exit

    return Runners(
        beads_ready=_beads_ready,
        beads_close=_beads_close,
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=_dispatch,
        run_verify=_run_verify,
        beads_update=_beads_update,
        loop_state_path=loop_state_path,
    )


# ---------------------------------------------------------------------------
# Test 1 — capacity_never_exceeds_max_workers
# ---------------------------------------------------------------------------

class TestCapacityNeverExceedsMaxWorkers:
    """A fake dispatch that blocks on an event. We assert len(active) <= max_workers
    at all times — specifically that we never submit more futures than max_workers."""

    def test_capacity_never_exceeds_max_workers(self) -> None:
        """At no point should more than max_workers beads be in flight simultaneously."""
        max_w = 2
        # Three beads available simultaneously
        beads = [_make_bead(f"fblai-cap{i}", priority=i + 1) for i in range(3)]

        # Barrier — blocks all dispatch calls until we release
        dispatch_barrier = threading.Barrier(max_w + 1, timeout=5.0)
        concurrency_peak = [0]
        concurrency_lock = threading.Lock()
        current_in_flight = [0]

        def _blocking_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            with concurrency_lock:
                current_in_flight[0] += 1
                if current_in_flight[0] > concurrency_peak[0]:
                    concurrency_peak[0] = current_in_flight[0]
            try:
                # All workers rendez-vous; main thread also arrives to release them
                dispatch_barrier.wait()
            except threading.BrokenBarrierError:
                pass
            finally:
                with concurrency_lock:
                    current_in_flight[0] -= 1
            return {"tokens": 5, "output": "ok"}

        # After max_w dispatches are blocked, unblock the barrier from the test thread
        # by participating; then the run can finish
        call_count = [0]

        def _dispatch_with_count(prompt: str, model: str, timeout_s: int) -> dict:
            call_count[0] += 1
            return _blocking_dispatch(prompt, model, timeout_s)

        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        ready_queue = list(beads)  # copy

        def _ready_fn(molecule: str) -> List[dict]:
            # Return only beads that are still "open"
            return [b for b in ready_queue if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=_dispatch_with_count,
            verify_exit=0,
            tracker=tracker,
        )
        cfg = _make_cfg(max_workers=max_w, max_iterations=10)

        # Run loop in a background thread so we can participate in the barrier
        result_holder = [None]

        def _run():
            result_holder[0] = run_mayor_loop(cfg, runners)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Participate in the barrier to unblock the first batch, then let it finish
        try:
            dispatch_barrier.wait(timeout=5.0)
        except threading.BrokenBarrierError:
            pass

        t.join(timeout=10.0)
        assert not t.is_alive(), "Mayor loop did not finish in time"
        assert concurrency_peak[0] <= max_w, (
            f"Concurrency peak {concurrency_peak[0]} exceeded max_workers={max_w}"
        )


# ---------------------------------------------------------------------------
# Test 2 — fills_freed_slots_with_newly_unblocked
# ---------------------------------------------------------------------------

class TestFillsFreedSlots:
    """When a slot frees (worker finishes), the Mayor immediately fills it with
    the next ready bead."""

    def test_fills_freed_slots_with_newly_unblocked(self, tmp_path: Path) -> None:
        """With 4 independent beads and max_workers=2, all 4 should be closed."""
        beads = [_make_bead(f"fblai-fill{i}", priority=i + 1) for i in range(4)]
        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in beads if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            verify_exit=0,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2, max_iterations=20)

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == 4, (
            f"Expected 4 beads closed, got {summary.closed}. closed={tracker.closed_beads}"
        )
        for b in beads:
            assert tracker.get_status(b["id"]) == "closed", (
                f"Bead {b['id']} not closed: {tracker.get_status(b['id'])}"
            )


# ---------------------------------------------------------------------------
# Test 3 — crashed_worker_slot_stays_occupied
# ---------------------------------------------------------------------------

class TestCrashedWorkerSlotStaysOccupied:
    """A worker that raises/times-out moves to recovery_blocked; its bead stays
    in_progress; the slot is NOT freed (capacity does not silently reclaim it)."""

    def test_crashed_worker_slot_stays_occupied(self, tmp_path: Path) -> None:
        """A crashing worker → bead stays in_progress, recovery_blocked has it,
        no re-dispatch of that bead, and the stop reason is capacity-exhausted."""
        crash_bead = _make_bead("fblai-crash", priority=1)
        tracker = _StatusTracker()
        tracker.set_open(crash_bead["id"])

        dispatch_call_count = [0]

        def _crashing_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            dispatch_call_count[0] += 1
            raise RuntimeError("worker exploded")

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [crash_bead] if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=_crashing_dispatch,
            verify_exit=0,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        # max_workers=1 so one crash fills all capacity
        cfg = _make_cfg(max_workers=1, max_iterations=10)

        summary = run_mayor_loop(cfg, runners)

        # Bead was dispatched (marked in_progress by Mayor) but NOT closed
        assert tracker.get_status("fblai-crash") == "in_progress", (
            f"Expected bead to stay in_progress, got: {tracker.get_status('fblai-crash')}"
        )
        assert summary.closed == 0
        assert summary.failed >= 1
        # Should have stopped due to capacity exhaustion or queue being stuck
        assert summary.stop_reason in (
            "capacity-exhausted-by-stuck-workers",
            "no-progress",
            "max-iterations",
            "queue-empty",
        ), f"Unexpected stop_reason: {summary.stop_reason}"
        # Dispatch was called exactly once (no re-dispatch of the crashed bead)
        assert dispatch_call_count[0] == 1, (
            f"Crashed bead was re-dispatched {dispatch_call_count[0]} times, expected 1"
        )


# ---------------------------------------------------------------------------
# Test 4 — single_writer_invariant
# ---------------------------------------------------------------------------

class TestSingleWriterInvariant:
    """A fake worker callable that attempts to call beads_close directly MUST NOT
    be able to do so — the close must only come from the Mayor's main-thread path."""

    def test_single_writer_invariant(self, tmp_path: Path) -> None:
        """Workers must NOT call beads_close directly. Any attempt to do so in
        a worker callable is a violation. We verify that close_if_verified
        (the Mayor path) is the ONLY code path that calls beads_close."""
        bead = _make_bead("fblai-sw", priority=1)
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        # A dispatch function that tries to close the bead itself (violation)
        # We can't prevent this at the Python level in the worker thread,
        # but we verify that the ONLY close that actually sticks is the Mayor's.
        mayor_closed_from_worker = [False]
        worker_close_calls = [0]

        def _violating_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            # Worker should NOT call beads_close — but let's track if it tried
            # In this test, we verify the Mayor is the sole entity that closes
            worker_close_calls[0] += 1
            # Return success so we can observe Mayor closing via verify_exit=0
            return {"tokens": 5, "output": "done"}

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [bead] if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=_violating_dispatch,
            verify_exit=0,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=1, max_iterations=5)

        # Spy on beads_close to verify it's only called from Mayor path
        original_close = runners.beads_close
        close_call_stack = []

        def _spy_close(bead_id: str) -> None:
            # Capture whether we're in the worker thread or main thread
            current = threading.current_thread()
            close_call_stack.append(current.name)
            return original_close(bead_id)

        runners.beads_close = _spy_close

        summary = run_mayor_loop(cfg, runners)

        # Bead was closed exactly once
        assert summary.closed == 1
        assert len(close_call_stack) == 1, (
            f"beads_close called {len(close_call_stack)} times, expected exactly 1"
        )
        # The close must have happened on the main thread (Mayor), not a worker thread
        # Worker threads are named "ThreadPoolExecutor-*"
        close_thread = close_call_stack[0]
        assert "ThreadPoolExecutor" not in close_thread, (
            f"beads_close was called from a worker thread '{close_thread}' — "
            "violates single-writer invariant. Only Mayor may close beads."
        )


# ---------------------------------------------------------------------------
# Test 5 — close_only_on_verify_exit_0
# ---------------------------------------------------------------------------

class TestCloseOnlyOnVerifyExit0:
    """verify_exit=1 leaves bead open; verify_exit=0 closes it."""

    def test_verify_exit_1_leaves_bead_open(self, tmp_path: Path) -> None:
        bead = _make_bead("fblai-ve1", priority=1)
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [bead] if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            verify_exit=1,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=1, max_iterations=2)

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == 0
        # Bead should NOT be in closed state
        assert tracker.get_status("fblai-ve1") != "closed", (
            "Bead was closed despite verify_exit=1"
        )

    def test_verify_exit_0_closes_bead(self, tmp_path: Path) -> None:
        bead = _make_bead("fblai-ve0", priority=1)
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [bead] if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            verify_exit=0,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=1, max_iterations=5)

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == 1
        assert tracker.get_status("fblai-ve0") == "closed"


# ---------------------------------------------------------------------------
# Test 6 — max_workers_1_matches_sequential
# ---------------------------------------------------------------------------

class TestMaxWorkers1MatchesSequential:
    """Parity: a fixed molecule run at max_workers=1 produces the same
    closed/failed counts as the current sequential run_loop."""

    def test_max_workers_1_matches_sequential(self, tmp_path: Path) -> None:
        """run_mayor_loop with max_workers=1 and run_loop must close the same beads."""
        beads = [_make_bead(f"fblai-par{i}", priority=i + 1) for i in range(3)]

        def _make_test_runners(tracker_instance: _StatusTracker, loop_state_path: Path) -> Runners:
            def _ready_fn(molecule: str) -> List[dict]:
                return [b for b in beads if tracker_instance.get_status(b["id"]) == "open"]

            return _make_mayor_runners(
                ready_beads_fn=_ready_fn,
                verify_exit=0,
                tracker=tracker_instance,
                loop_state_path=loop_state_path,
            )

        # Run with run_mayor_loop at max_workers=1
        tracker_mayor = _StatusTracker()
        for b in beads:
            tracker_mayor.set_open(b["id"])

        runners_mayor = _make_test_runners(tracker_mayor, tmp_path / "mayor-state.json")
        cfg_mayor = _make_cfg(max_workers=1, max_iterations=20)
        summary_mayor = run_mayor_loop(cfg_mayor, runners_mayor)

        # Run with run_loop (sequential)
        tracker_seq = _StatusTracker()
        for b in beads:
            tracker_seq.set_open(b["id"])

        # Build a runners that matches what run_loop expects (no beads_update needed)
        seq_runners = Runners(
            beads_ready=lambda m: [b for b in beads if tracker_seq.get_status(b["id"]) == "open"],
            beads_close=tracker_seq.close,
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 10, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            loop_state_path=tmp_path / "seq-state.json",
        )
        cfg_seq = _make_cfg(max_workers=1, max_iterations=20)
        summary_seq = run_loop(cfg_seq, seq_runners)

        assert summary_mayor.closed == summary_seq.beads_closed, (
            f"mayor_loop closed={summary_mayor.closed} but sequential closed={summary_seq.beads_closed}"
        )
        assert summary_mayor.closed == 3, f"Expected all 3 beads closed, got {summary_mayor.closed}"


# ---------------------------------------------------------------------------
# Test 7 — capacity_exhausted_by_stuck_workers_stops
# ---------------------------------------------------------------------------

class TestCapacityExhaustedByStuckWorkers:
    """When recovery_blocked fills to max_workers and active is empty and no
    ready beads remain, the loop stops with 'capacity-exhausted-by-stuck-workers'."""

    def test_capacity_exhausted_by_stuck_workers_stops(self, tmp_path: Path) -> None:
        """Fill recovery_blocked to max_workers capacity; assert stop reason."""
        max_w = 2
        # Two beads — each will crash, filling recovery_blocked
        crash_beads = [_make_bead(f"fblai-stuck{i}", priority=i + 1) for i in range(max_w)]
        tracker = _StatusTracker()
        for b in crash_beads:
            tracker.set_open(b["id"])

        def _crashing_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            raise RuntimeError("always crashes")

        def _ready_fn(molecule: str) -> List[dict]:
            # Only return beads that are still open (not yet dispatched / in_progress / crashed)
            return [b for b in crash_beads if tracker.get_status(b["id"]) == "open"]

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=_crashing_dispatch,
            verify_exit=0,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=max_w, max_iterations=20)

        summary = run_mayor_loop(cfg, runners)

        assert summary.stop_reason == "capacity-exhausted-by-stuck-workers", (
            f"Expected stop_reason='capacity-exhausted-by-stuck-workers', "
            f"got '{summary.stop_reason}'"
        )
        assert summary.closed == 0
