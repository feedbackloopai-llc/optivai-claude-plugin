"""test_worktree_isolation.py — P1.2 per-worker git-worktree isolation tests.

Tests for bead fblai-rbvj9:
1. Two concurrent workers receive DISTINCT worktree paths (no shared cwd).
2. git worktree add/remove calls are serialized (no overlapping creates).
3. Worktrees are torn down after the worker finishes (and on worker error).
4. Single-writer invariant preserved (no bead-status mutation from worker threads).

VA0b lifecycle note (2026-06-22):
  Tests 1–4 above exercise the LEGACY context-manager path (_mayor_worker with
  worktree_manager set, worktree_create=None).  In the legacy path, teardown is
  controlled by the context manager and fires when _mayor_worker exits, which is
  the behavior these tests assert.

  The NEW VA0b lifecycle (worktree_create / worktree_teardown / merge_branch) is
  tested in test_worktree_integration.py, where teardown is the Mayor's
  responsibility and fires AFTER the merge decision.

  These isolation guarantees all hold in both paths:
    - Workers receive DISTINCT worktree paths.
    - git worktree add/remove is serialized through _WORKTREE_LOCK.
    - Single-writer invariant: workers never call beads_close or beads_update.

Run: python3 -m pytest scripts/tests/test_worktree_isolation.py -q
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

# Ensure scripts/ and hooks/ are on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    RunConfig,
    Runners,
    WorkerResult,
    _mayor_worker,
    _live_worktree_manager,
    _WORKTREE_LOCK,
    run_mayor_loop,
    MayorSummary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bead(
    bead_id: str = "fblai-wt-test",
    title: str = "Worktree test bead",
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


class _LockOrderTracker:
    """Tracks lock acquisition order and detects overlapping critical sections."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active_count = 0
        self._peak_concurrent = 0
        self._acquire_events: List[float] = []
        self._release_events: List[float] = []
        self._overlap_detected = False

    def acquire(self) -> None:
        with self._lock:
            self._active_count += 1
            if self._active_count > self._peak_concurrent:
                self._peak_concurrent = self._active_count
            if self._active_count > 1:
                self._overlap_detected = True
            self._acquire_events.append(time.monotonic())

    def release(self) -> None:
        with self._lock:
            self._active_count -= 1
            self._release_events.append(time.monotonic())

    @property
    def peak_concurrent(self) -> int:
        with self._lock:
            return self._peak_concurrent

    @property
    def overlap_detected(self) -> bool:
        with self._lock:
            return self._overlap_detected

    @property
    def total_acquires(self) -> int:
        with self._lock:
            return len(self._acquire_events)


class _StatusTracker:
    """Thread-safe tracker of bead statuses."""

    def __init__(self):
        self._lock = threading.Lock()
        self._statuses: dict[str, str] = {}
        self._closed: List[str] = []
        self._updated: List[tuple[str, str]] = []

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


def _make_fake_worktree_manager(
    lock_tracker: _LockOrderTracker,
    worktree_registry: dict,
    *,
    fail_on_error: bool = False,
):
    """Return a context manager factory that records worktree paths and tracks lock usage.

    Parameters
    ----------
    lock_tracker : tracks overlapping critical sections (must be serial, never parallel)
    worktree_registry : mutable dict mapping bead_id → path assigned
    fail_on_error : if True, the worktree_manager raises inside the context (tests teardown)
    """
    @contextlib.contextmanager
    def _manager(bead_id: str):
        # Simulate serialized creation (hold the tracker during 'creation')
        lock_tracker.acquire()
        wt_path = f"/tmp/fake-wt-{bead_id}-{threading.get_ident()}"
        worktree_registry[bead_id] = wt_path
        lock_tracker.release()

        try:
            if fail_on_error:
                raise RuntimeError(f"Simulated dispatch failure in worktree for {bead_id}")
            yield wt_path
        finally:
            # Simulate serialized teardown
            lock_tracker.acquire()
            # Mark as torn down by removing from registry
            worktree_registry.pop(bead_id, None)
            lock_tracker.release()

    return _manager


def _make_runners_with_worktree(
    *,
    tracker: _StatusTracker,
    lock_tracker: _LockOrderTracker,
    worktree_registry: dict,
    dispatch_fn=None,
    verify_exit: int = 0,
    wt_fail: bool = False,
    loop_state_path: Optional[Path] = None,
) -> Runners:
    """Build Runners with a fake worktree_manager and dispatch_with_cwd."""
    wt_mgr = _make_fake_worktree_manager(lock_tracker, worktree_registry, fail_on_error=wt_fail)

    cwd_received: List[Optional[str]] = []

    def _dispatch_with_cwd(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
        cwd_received.append(cwd)
        if dispatch_fn is not None:
            return dispatch_fn(prompt, model, timeout_s, cwd)
        return {"tokens": 10, "output": "done"}

    def _fail_on_worker_bead_write(label: str):
        def _fn(*args, **kwargs):
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"{label} called from worker thread '{t.name}' — single-writer violation"
            )
        return _fn

    runners = Runners(
        beads_ready=MagicMock(return_value=[]),
        beads_close=_fail_on_worker_bead_write("beads_close"),
        beads_update=_fail_on_worker_bead_write("beads_update"),
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=lambda p, m, t: _dispatch_with_cwd(p, m, t, None),
        run_verify=MagicMock(return_value=verify_exit),
        worktree_manager=wt_mgr,
        dispatch_with_cwd=_dispatch_with_cwd,
        loop_state_path=loop_state_path,
    )
    # Expose cwd_received for assertions
    runners._cwd_received = cwd_received  # type: ignore[attr-defined]
    return runners


# ---------------------------------------------------------------------------
# Test 1 — Two concurrent workers receive DISTINCT worktree paths
# ---------------------------------------------------------------------------

class TestDistinctWorktreePaths:
    """Concurrent workers must never share a cwd."""

    def test_two_concurrent_workers_get_distinct_paths(self) -> None:
        """Dispatch two beads concurrently; their worktree paths must differ."""
        beads = [
            _make_bead("fblai-wt-a", title="Implement feature A", priority=1),
            _make_bead("fblai-wt-b", title="Implement feature B", priority=2),
        ]
        cfg = _make_cfg(max_workers=2)

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        # Collect (bead_id, cwd) pairs from concurrent dispatches
        received_paths: List[tuple[str, Optional[str]]] = []
        path_lock = threading.Lock()
        # Barrier ensures both workers are in dispatch simultaneously
        barrier = threading.Barrier(2, timeout=5.0)

        def _sync_dispatch(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            bead_id = None
            # Extract bead_id from the prompt (it's embedded in the objective line)
            for line in prompt.splitlines():
                if "bead " in line and " in " in line:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1].startswith("fblai-"):
                        bead_id = parts[1]
                        break
            with path_lock:
                received_paths.append((bead_id or "unknown", cwd))
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return {"tokens": 5, "output": "done"}

        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in beads if tracker.get_status(b["id"]) == "open"]

        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )
        runners.beads_close = tracker.close
        runners.beads_update = tracker.update
        runners.beads_ready = _ready_fn
        runners.dispatch_with_cwd = _sync_dispatch
        runners.dispatch = lambda p, m, t: _sync_dispatch(p, m, t, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_mayor_worker, bead, cfg, runners)
                for bead in beads
            ]
            results = [f.result(timeout=10) for f in futures]

        # Both workers must have been given a cwd (worktree path)
        cwd_values = [path for _, path in received_paths if path is not None]
        assert len(cwd_values) == 2, (
            f"Expected 2 workers with worktree paths, got: {received_paths}"
        )
        # Paths must be distinct
        assert cwd_values[0] != cwd_values[1], (
            f"Workers received the SAME worktree path: {cwd_values[0]!r} — isolation violated"
        )

    def test_worker_cwd_is_the_worktree_path(self) -> None:
        """dispatch_with_cwd receives the exact path yielded by worktree_manager."""
        bead = _make_bead("fblai-wt-cwd")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}
        cwd_passed: List[Optional[str]] = []

        def _capture_cwd(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            cwd_passed.append(cwd)
            return {"tokens": 5, "output": "done"}

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )
        runners.dispatch_with_cwd = _capture_cwd
        runners.dispatch = lambda p, m, t: _capture_cwd(p, m, t, None)

        result = _mayor_worker(bead, cfg, runners)

        assert result.error is None, f"Worker should not error: {result.error}"
        assert len(cwd_passed) == 1
        # The cwd must be a non-empty string (the worktree path)
        assert cwd_passed[0] is not None
        assert isinstance(cwd_passed[0], str)
        assert len(cwd_passed[0]) > 0


# ---------------------------------------------------------------------------
# Test 2 — Worktree add/remove calls are serialized
# ---------------------------------------------------------------------------

class TestWorktreeSerializationLock:
    """git worktree add and remove must be serialized — never concurrent."""

    def test_creation_never_overlaps_for_concurrent_workers(self) -> None:
        """When N workers start concurrently, worktree creation is never overlapping."""
        n_workers = 4
        beads = [
            _make_bead(f"fblai-serial-{i}", title=f"Implement item {i}", priority=i + 1)
            for i in range(n_workers)
        ]
        cfg = _make_cfg(max_workers=n_workers)

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        # Hold a barrier inside dispatch so all workers attempt worktree creation 'simultaneously'
        dispatch_barrier = threading.Barrier(n_workers, timeout=5.0)

        def _sync_dispatch(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            try:
                dispatch_barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return {"tokens": 5, "output": "done"}

        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )
        runners.dispatch_with_cwd = _sync_dispatch
        runners.dispatch = lambda p, m, t: _sync_dispatch(p, m, t, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [
                pool.submit(_mayor_worker, bead, cfg, runners)
                for bead in beads
            ]
            results = [f.result(timeout=15) for f in futures]

        # The lock_tracker's acquire/release pattern was serialized
        assert not lock_tracker.overlap_detected, (
            f"Worktree creation/teardown overlapped — serialization violated. "
            f"Peak concurrent: {lock_tracker.peak_concurrent}"
        )
        assert lock_tracker.peak_concurrent <= 1, (
            f"Peak concurrent worktree operations was {lock_tracker.peak_concurrent}, "
            f"expected at most 1 (serialized)"
        )

    def test_lock_acquired_for_each_worktree_create_and_teardown(self) -> None:
        """Each worker triggers exactly 2 lock acquires: one for creation, one for teardown."""
        n_workers = 3
        beads = [
            _make_bead(f"fblai-lockcount-{i}", title=f"Task {i}", priority=i + 1)
            for i in range(n_workers)
        ]
        cfg = _make_cfg(max_workers=n_workers)

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [
                pool.submit(_mayor_worker, bead, cfg, runners)
                for bead in beads
            ]
            [f.result(timeout=10) for f in futures]

        # Each worker: 1 acquire for creation + 1 acquire for teardown = 2 per worker
        expected_total = n_workers * 2
        assert lock_tracker.total_acquires == expected_total, (
            f"Expected {expected_total} lock acquires ({n_workers} workers × 2), "
            f"got {lock_tracker.total_acquires}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Worktrees are torn down after worker finishes (including on error)
# ---------------------------------------------------------------------------

class TestWorktreeTeardown:
    """Worktrees must be removed after each worker, both on success and on error."""

    def test_worktree_removed_after_successful_worker(self) -> None:
        """On worker success, the worktree (registry entry) must be gone after completion."""
        bead = _make_bead("fblai-td-ok")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}  # populated on create, removed on teardown

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=0,
        )

        result = _mayor_worker(bead, cfg, runners)

        assert result.error is None
        # Registry entry must have been cleaned up
        assert bead["id"] not in worktree_registry, (
            f"Worktree for {bead['id']} was not torn down after successful completion. "
            f"Registry: {worktree_registry}"
        )

    def test_worktree_removed_after_worker_dispatch_exception(self) -> None:
        """When dispatch raises, the worktree must still be torn down."""
        bead = _make_bead("fblai-td-exc")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        def _raising_dispatch(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            raise RuntimeError("dispatch failure")

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )
        runners.dispatch_with_cwd = _raising_dispatch
        runners.dispatch = lambda p, m, t: _raising_dispatch(p, m, t, None)

        result = _mayor_worker(bead, cfg, runners)

        # Worker returns an error result, not a raise
        assert result.error is not None
        # Worktree must still be torn down
        assert bead["id"] not in worktree_registry, (
            f"Worktree for {bead['id']} was NOT torn down after dispatch exception. "
            f"Registry: {worktree_registry}"
        )

    def test_worktree_removed_after_verify_failure(self) -> None:
        """When verify exits non-zero, worktree is still torn down."""
        bead = _make_bead("fblai-td-vfail")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=1,
        )

        result = _mayor_worker(bead, cfg, runners)

        assert result.verify_exit == 1
        assert bead["id"] not in worktree_registry, (
            "Worktree was not torn down after verify failure"
        )

    def test_all_worktrees_torn_down_in_concurrent_run(self) -> None:
        """After a concurrent batch of workers, zero worktrees remain in the registry."""
        n = 5
        beads = [
            _make_bead(f"fblai-batch-{i}", title=f"Batch task {i}", priority=i + 1)
            for i in range(n)
        ]
        cfg = _make_cfg(max_workers=n)

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}

        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=0,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            futures = [
                pool.submit(_mayor_worker, bead, cfg, runners)
                for bead in beads
            ]
            [f.result(timeout=10) for f in futures]

        assert len(worktree_registry) == 0, (
            f"{len(worktree_registry)} worktree(s) were NOT torn down: {worktree_registry}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Single-writer invariant preserved from worker threads
# ---------------------------------------------------------------------------

class TestSingleWriterPreservedWithWorktree:
    """The worktree feature must not introduce any bead-status writes from worker threads."""

    def test_workers_with_worktree_do_not_call_beads_close(self) -> None:
        """_mayor_worker with worktree isolation must not call beads_close."""
        bead = _make_bead("fblai-wt-sw-close")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}
        beads_close_calls: List[str] = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=0,
        )
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0, (
            f"beads_close was called {len(beads_close_calls)} time(s) from "
            "_mayor_worker with worktree — single-writer violation"
        )

    def test_workers_with_worktree_do_not_call_beads_update(self) -> None:
        """_mayor_worker with worktree isolation must not call beads_update."""
        bead = _make_bead("fblai-wt-sw-update")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}
        beads_update_calls: List[tuple[str, str]] = []

        def _spy_update(bead_id: str, status: str) -> None:
            beads_update_calls.append((bead_id, status))

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=0,
        )
        runners.beads_update = _spy_update

        _mayor_worker(bead, cfg, runners)

        assert len(beads_update_calls) == 0, (
            f"beads_update was called {len(beads_update_calls)} time(s) from "
            "_mayor_worker with worktree — single-writer violation"
        )

    def test_concurrent_workers_with_worktree_beads_close_only_on_main_thread(
        self, tmp_path: Path
    ) -> None:
        """In run_mayor_loop, beads_close is always called from the main thread,
        never from a worker — even with worktree_manager wired up."""
        beads = [
            _make_bead(f"fblai-wt-main-{i}", title=f"Task {i}", priority=i + 1)
            for i in range(3)
        ]
        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        close_thread_names: List[str] = []
        close_lock = threading.Lock()

        def _spy_close(bead_id: str) -> None:
            with close_lock:
                close_thread_names.append(threading.current_thread().name)
            tracker.close(bead_id)

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in beads if tracker.get_status(b["id"]) == "open"]

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}
        cfg = _make_cfg(max_workers=2, max_iterations=20)

        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
            verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        runners.beads_ready = _ready_fn
        runners.beads_close = _spy_close
        runners.beads_update = tracker.update

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == 3, f"Expected 3 closed, got {summary.closed}"

        # Every close must have been called from the main thread (not ThreadPoolExecutor)
        for thread_name in close_thread_names:
            assert "ThreadPoolExecutor" not in thread_name, (
                f"beads_close was called from worker thread '{thread_name}' — "
                "single-writer invariant violated in concurrent worktree run"
            )

    def test_failed_worker_with_worktree_does_not_call_beads_close(self) -> None:
        """A failed worker (dispatch raises) with worktree must not call beads_close."""
        bead = _make_bead("fblai-wt-fail-sw")
        cfg = _make_cfg()

        lock_tracker = _LockOrderTracker()
        worktree_registry: dict = {}
        beads_close_calls: List[str] = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        def _raising_dispatch(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            raise RuntimeError("failure")

        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        runners = _make_runners_with_worktree(
            tracker=tracker,
            lock_tracker=lock_tracker,
            worktree_registry=worktree_registry,
        )
        runners.dispatch_with_cwd = _raising_dispatch
        runners.dispatch = lambda p, m, t: _raising_dispatch(p, m, t, None)
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0, (
            "beads_close called from worker despite dispatch failure"
        )


# ---------------------------------------------------------------------------
# Test 5 — No worktree_manager → plain dispatch (backward compat)
# ---------------------------------------------------------------------------

class TestNoWorktreeManagerFallback:
    """When runners.worktree_manager is None, _mayor_worker falls back to plain dispatch
    and no cwd is passed (backward-compatible path)."""

    def test_no_worktree_manager_uses_plain_dispatch(self) -> None:
        """With worktree_manager=None, dispatch is called without cwd."""
        bead = _make_bead("fblai-no-wt")
        cfg = _make_cfg()
        dispatch_calls: List[tuple] = []

        def _spy_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            dispatch_calls.append((prompt, model, timeout_s))
            return {"tokens": 5, "output": "done"}

        runners = Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=_spy_dispatch,
            run_verify=MagicMock(return_value=0),
            worktree_manager=None,    # explicitly disabled
            dispatch_with_cwd=None,
        )

        result = _mayor_worker(bead, cfg, runners)

        assert result.error is None
        assert len(dispatch_calls) == 1, (
            f"Expected exactly 1 dispatch call, got {len(dispatch_calls)}"
        )

    def test_no_worktree_dispatch_with_cwd_still_succeeds(self) -> None:
        """dispatch_with_cwd=None + worktree_manager=None → normal operation."""
        bead = _make_bead("fblai-no-wt-cwd")
        cfg = _make_cfg()

        runners = Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            worktree_manager=None,
            dispatch_with_cwd=None,
        )

        result = _mayor_worker(bead, cfg, runners)
        assert result.error is None
        assert result.verify_exit == 0


# ---------------------------------------------------------------------------
# Test 6 — Real git worktree lifecycle (integration, uses tmp_path repo)
# ---------------------------------------------------------------------------

class TestLiveWorktreeManager:
    """Exercise _live_worktree_manager with a real throwaway git repository.
    Uses tmp_path to avoid touching the actual repo.
    """

    @staticmethod
    def _init_bare_repo(tmp_path: Path) -> Path:
        """Create a minimal git repo with one commit so worktree add works."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", str(repo_dir)], capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.email", "test@test.com"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
            capture_output=True, check=True,
        )
        # Create an initial commit so HEAD exists (worktree add --detach requires HEAD)
        (repo_dir / "README.md").write_text("init\n")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "README.md"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "init"],
            capture_output=True, check=True,
        )
        return repo_dir

    def test_live_worktree_create_and_teardown(self, tmp_path: Path) -> None:
        """_live_worktree_manager creates a real worktree and removes it on exit."""
        try:
            repo_dir = self._init_bare_repo(tmp_path)
        except subprocess.CalledProcessError:
            pytest.skip("git not available or repo init failed")

        # Patch rev-parse so _live_worktree_manager uses our repo
        import unittest.mock as mock
        orig_run = subprocess.run
        worktree_path_inside: List[Optional[str]] = []
        worktree_existed_inside: List[bool] = []

        def _patched_run(args, **kwargs):
            # Redirect rev-parse to our repo
            if isinstance(args, list) and "rev-parse" in args:
                result = orig_run(
                    ["git", "-C", str(repo_dir), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=10,
                )
                return result
            # Redirect git worktree add/remove to our repo by fixing cwd
            if isinstance(args, list) and args[0] == "git" and len(args) > 1:
                if args[1] == "worktree":
                    # Replace or inject the correct repo root
                    new_kwargs = dict(kwargs)
                    new_kwargs["cwd"] = str(repo_dir)
                    return orig_run(args, **new_kwargs)
            return orig_run(args, **kwargs)

        with mock.patch("loop_runner.subprocess.run", side_effect=_patched_run):
            with _live_worktree_manager("fblai-live-wt") as wt_path:
                worktree_path_inside.append(wt_path)
                if wt_path is not None:
                    worktree_existed_inside.append(Path(wt_path).exists())

        if worktree_path_inside[0] is None:
            pytest.skip("git worktree add failed in test environment")

        # After context exit, the worktree directory must not exist
        assert not Path(worktree_path_inside[0]).exists(), (
            f"Worktree path {worktree_path_inside[0]} still exists after teardown"
        )

    def test_live_worktree_two_beads_get_distinct_paths(self, tmp_path: Path) -> None:
        """Two sequential live worktrees for different beads yield different paths."""
        try:
            repo_dir = self._init_bare_repo(tmp_path)
        except subprocess.CalledProcessError:
            pytest.skip("git not available or repo init failed")

        import unittest.mock as mock

        paths: List[Optional[str]] = []

        orig_run = subprocess.run

        def _patched_run(args, **kwargs):
            if isinstance(args, list) and "rev-parse" in args:
                return orig_run(
                    ["git", "-C", str(repo_dir), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=10,
                )
            if isinstance(args, list) and args[0] == "git" and len(args) > 1:
                if args[1] == "worktree":
                    new_kwargs = dict(kwargs)
                    new_kwargs["cwd"] = str(repo_dir)
                    return orig_run(args, **new_kwargs)
            return orig_run(args, **kwargs)

        with mock.patch("loop_runner.subprocess.run", side_effect=_patched_run):
            with _live_worktree_manager("fblai-live-wt-x") as p1:
                paths.append(p1)
            with _live_worktree_manager("fblai-live-wt-y") as p2:
                paths.append(p2)

        if None in paths:
            pytest.skip("git worktree add failed in test environment")

        assert paths[0] != paths[1], (
            f"Two worktrees got the same path: {paths[0]!r}"
        )


# ---------------------------------------------------------------------------
# Test 7 — VA0b lifecycle: worker does NOT tear down (Mayor controls teardown)
# ---------------------------------------------------------------------------

class TestVA0bWorkerDoesNotTeardown:
    """In the VA0b lifecycle (worktree_create set), _mayor_worker must NOT tear down
    the worktree.  Teardown is the Mayor's responsibility after merge decision.

    These tests use fake worktree_create / worktree_teardown to confirm the
    lifecycle separation without needing a real git repo.
    """

    def test_worker_does_not_call_worktree_teardown(self) -> None:
        """With VA0b runners, _mayor_worker must not call worktree_teardown."""
        teardown_calls: List[tuple] = []
        create_calls: List[str] = []

        def _fake_create(bead_id: str) -> Optional[tuple]:
            create_calls.append(bead_id)
            return (f"/tmp/fake-wt-{bead_id}", f"mayor/{bead_id}")

        def _fake_teardown(path: str, branch: str) -> None:
            teardown_calls.append((path, branch))

        bead = _make_bead("fblai-va0b-no-teardown")
        cfg = _make_cfg()

        runners = Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            beads_update=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            run_verify_in_cwd=MagicMock(return_value=0),
            dispatch_with_cwd=MagicMock(return_value={"tokens": 5, "output": "done"}),
            worktree_create=_fake_create,
            worktree_teardown=_fake_teardown,
            merge_branch=MagicMock(return_value=0),
        )

        result = _mayor_worker(bead, cfg, runners)

        # Worker should have created the worktree
        assert len(create_calls) == 1
        # Worker must NOT have called teardown — that's the Mayor's job
        assert len(teardown_calls) == 0, (
            f"worktree_teardown was called {len(teardown_calls)} time(s) from "
            "_mayor_worker — in the VA0b lifecycle, only the Mayor tears down"
        )
        # Worker must NOT have called merge_branch
        runners.merge_branch.assert_not_called()
        # Worker must return branch and path coordinates for the Mayor
        assert result.branch_name == f"mayor/{bead['id']}"
        assert result.worktree_path is not None

    def test_worker_returns_branch_name_and_worktree_path(self) -> None:
        """WorkerResult must carry branch_name and worktree_path for the Mayor."""
        bead_id = "fblai-va0b-coords"
        bead = _make_bead(bead_id)
        cfg = _make_cfg()

        expected_path = f"/tmp/fake-wt-{bead_id}"
        expected_branch = f"mayor/{bead_id}"

        runners = Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            beads_update=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            run_verify_in_cwd=MagicMock(return_value=0),
            dispatch_with_cwd=MagicMock(return_value={"tokens": 5, "output": "done"}),
            worktree_create=lambda bid: (expected_path, expected_branch),
            worktree_teardown=MagicMock(),
            merge_branch=MagicMock(return_value=0),
        )

        result = _mayor_worker(bead, cfg, runners)

        assert result.branch_name == expected_branch, (
            f"Expected branch_name={expected_branch!r}, got {result.branch_name!r}"
        )
        assert result.worktree_path == expected_path, (
            f"Expected worktree_path={expected_path!r}, got {result.worktree_path!r}"
        )
        assert result.error is None

    def test_va0b_path_when_worktree_create_set_not_legacy(self) -> None:
        """When worktree_create is set, _mayor_worker uses VA0b path not legacy path.

        Confirms by checking worktree_manager (legacy) is NOT called when
        worktree_create is set.
        """
        legacy_wt_calls: List[str] = []

        import contextlib

        @contextlib.contextmanager
        def _legacy_manager(bead_id: str):
            legacy_wt_calls.append(bead_id)
            yield "/tmp/legacy-path"

        bead = _make_bead("fblai-va0b-path-check")
        cfg = _make_cfg()

        runners = Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            beads_update=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            run_verify_in_cwd=MagicMock(return_value=0),
            dispatch_with_cwd=MagicMock(return_value={"tokens": 5, "output": "done"}),
            worktree_manager=_legacy_manager,     # legacy path — must NOT be called
            worktree_create=lambda bid: ("/tmp/new-path", f"mayor/{bid}"),  # VA0b path
            worktree_teardown=MagicMock(),
            merge_branch=MagicMock(return_value=0),
        )

        _mayor_worker(bead, cfg, runners)

        assert len(legacy_wt_calls) == 0, (
            f"Legacy worktree_manager was called {len(legacy_wt_calls)} time(s) "
            "despite worktree_create being set — VA0b path not taken"
        )
