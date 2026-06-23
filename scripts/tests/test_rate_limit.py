"""test_rate_limit.py — Acceptance tests for VA1 rate-limit-aware backpressure.

VA1 contract (docs/plans/2026-06-22-mayor-v2.md):
  - classify RATE_LIMITED ≠ FAILED
  - governor `rate-limited` pause-stop
  - clean resume (never burn a bead)

Coverage:
  1. is_rate_limited classifier — structured flag, output text, exception, negatives
  2. worker classifies a rate-limit from the dispatch RETURN dict (V is skipped)
  3. worker classifies a rate-limit raised as an EXCEPTION (not a crash)
  4. Mayor returns a rate-limited bead to the ready set (open) — never failed/closed
  5. governor pause-stops with stop_reason == "rate-limited"
  6. should_continue_mayor returns (False, "rate-limited") when summary.rate_limited
  7. RATE_LIMITED is distinct from FAILED (a real V-fail is NOT rate-limited)
  8. clean resume: a paused bead re-dispatches and closes on the next run
  9. worktree teardown discards partial code on rate-limit (VA0b lifecycle)
 10. live dispatch surfaces a structured rate_limited flag from the CLI response
 11. a rate-limit pause is a clean (exit 0) outcome in main()

All tests use injected fakes — no real subprocesses.

Run: python3 -m pytest scripts/tests/test_rate_limit.py -q
"""

from __future__ import annotations

import sys
import threading
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
    MayorSummary,
    RunConfig,
    Runners,
    WorkerResult,
    _mayor_worker,
    is_rate_limited,
    run_mayor_loop,
    should_continue_mayor,
)


# ---------------------------------------------------------------------------
# Shared test helpers (mirror test_governor.py conventions)
# ---------------------------------------------------------------------------

def _make_bead(
    bead_id: str = "fblai-rl",
    title: str = "Rate-limit test bead",
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
    verify_spy=None,
    worktree_create=None,
    worktree_teardown=None,
    merge_branch=None,
    run_verify_in_cwd=None,
    dispatch_with_cwd=None,
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
        if verify_spy is not None:
            verify_spy()
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
        worktree_create=worktree_create,
        worktree_teardown=worktree_teardown,
        merge_branch=merge_branch,
        run_verify_in_cwd=run_verify_in_cwd,
        dispatch_with_cwd=dispatch_with_cwd,
    )


# ---------------------------------------------------------------------------
# 1 — is_rate_limited classifier
# ---------------------------------------------------------------------------

class TestClassifier:
    @pytest.mark.parametrize(
        "text",
        [
            "API error: 429 Too Many Requests",
            "anthropic.RateLimitError: rate_limit_error",
            "Claude AI usage limit reached. Try again later.",
            "Error: rate limit exceeded",
            "got a rate-limit response",
            "quota exceeded for this org",
        ],
    )
    def test_positive_signatures_in_output(self, text: str) -> None:
        assert is_rate_limited({"output": text}, None) is True

    def test_structured_flag_wins_even_with_clean_text(self) -> None:
        # The explicit structured flag is honoured regardless of the text body.
        assert is_rate_limited({"output": "all good", "rate_limited": True}, None) is True

    @pytest.mark.parametrize(
        "text",
        [
            "task completed successfully",
            "wrote 12 files, all tests pass",
            "",
        ],
    )
    def test_negative_clean_output(self, text: str) -> None:
        assert is_rate_limited({"output": text}, None) is False

    def test_exception_signature(self) -> None:
        assert is_rate_limited(None, RuntimeError("HTTP 429: too many requests")) is True
        assert is_rate_limited(None, RuntimeError("dispatch failed: timed out")) is False

    def test_none_inputs(self) -> None:
        assert is_rate_limited(None, None) is False

    def test_falsy_structured_flag_falls_back_to_text(self) -> None:
        # rate_limited=False but text carries a signature → still True (text scan).
        assert is_rate_limited(
            {"output": "got 429 back", "rate_limited": False}, None
        ) is True
        assert is_rate_limited(
            {"output": "clean", "rate_limited": False}, None
        ) is False


# ---------------------------------------------------------------------------
# 2 / 3 — worker classification
# ---------------------------------------------------------------------------

class TestWorkerClassification:
    def test_worker_flags_rate_limit_from_return_dict_and_skips_verify(self) -> None:
        """A rate-limited dispatch RETURN dict → WorkerResult.rate_limited; V is NOT run."""
        verify_calls = [0]

        def _dispatch(prompt, model, timeout_s):
            return {"tokens": 0, "output": "Claude AI usage limit reached"}

        def _run_verify(cmd, timeout_s):
            verify_calls[0] += 1
            return 0

        runners = Runners(
            beads_ready=lambda m: [],
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=_dispatch,
            run_verify=_run_verify,
            beads_update=MagicMock(),
        )
        cfg = _make_cfg(max_workers=1)
        res: WorkerResult = _mayor_worker(_make_bead(), cfg, runners)

        assert res.rate_limited is True
        assert res.error is None
        assert res.verify_exit is None
        assert verify_calls[0] == 0, "V must NOT run on a rate-limited dispatch"

    def test_worker_flags_rate_limit_from_exception_not_as_crash(self) -> None:
        """A rate-limit raised as an exception → rate_limited (NOT error/crash)."""
        def _dispatch(prompt, model, timeout_s):
            raise RuntimeError("API error 429 rate_limit_error")

        runners = Runners(
            beads_ready=lambda m: [],
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=_dispatch,
            run_verify=lambda c, t: 0,
            beads_update=MagicMock(),
        )
        cfg = _make_cfg(max_workers=1)
        res: WorkerResult = _mayor_worker(_make_bead(), cfg, runners)

        assert res.rate_limited is True
        assert res.error is None, "rate-limit exception must NOT be classed as a crash"

    def test_worker_non_rate_limit_exception_is_still_a_crash(self) -> None:
        """A non-rate-limit exception keeps the existing crash semantics."""
        def _dispatch(prompt, model, timeout_s):
            raise RuntimeError("boom: segfault")

        runners = Runners(
            beads_ready=lambda m: [],
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=_dispatch,
            run_verify=lambda c, t: 0,
            beads_update=MagicMock(),
        )
        cfg = _make_cfg(max_workers=1)
        res: WorkerResult = _mayor_worker(_make_bead(), cfg, runners)

        assert res.rate_limited is False
        assert res.error is not None


# ---------------------------------------------------------------------------
# 4 / 5 — Mayor backpressure: never burn the bead + governor pause-stop
# ---------------------------------------------------------------------------

class TestMayorBackpressure:
    def test_rate_limited_bead_returned_to_open_and_run_pauses(
        self, tmp_path: Path
    ) -> None:
        bead = _make_bead("fblai-rl-1")
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [bead] if tracker.get_status(bead["id"]) == "open" else []

        def _dispatch(prompt, model, timeout_s):
            return {"tokens": 0, "output": "Error: 429 rate limit exceeded"}

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=_dispatch,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2, max_iterations=20)

        summary = run_mayor_loop(cfg, runners)

        # Governor pause-stopped.
        assert summary.stop_reason == "rate-limited"
        assert summary.rate_limited is True
        assert summary.rate_limited_beads == 1
        # Never burned: not closed, not counted as failed; back in the ready set.
        assert summary.closed == 0
        assert summary.failed == 0
        assert bead["id"] not in tracker.closed_beads
        assert tracker.get_status(bead["id"]) == "open"
        # The Mayor explicitly reset it to open (single-writer).
        assert ("fblai-rl-1", "open") in tracker.update_history

    def test_governor_pause_stop_predicate(self) -> None:
        """should_continue_mayor returns (False, 'rate-limited') when flagged."""
        cfg = _make_cfg(max_workers=2)
        s = MayorSummary()
        # Not flagged → continue.
        ok, reason = should_continue_mayor(s, cfg, active={}, recovery_blocked=set())
        assert ok is True and reason == ""
        # Flagged → pause-stop.
        s.rate_limited = True
        ok, reason = should_continue_mayor(s, cfg, active={}, recovery_blocked=set())
        assert ok is False and reason == "rate-limited"


# ---------------------------------------------------------------------------
# 7 — RATE_LIMITED is distinct from FAILED
# ---------------------------------------------------------------------------

class TestDistinctFromFailed:
    def test_real_verify_failure_is_not_rate_limited(self, tmp_path: Path) -> None:
        """A genuine V-fail (exit 1) is FAILED, not rate-limited — bead not paused."""
        bead = _make_bead("fblai-fail-1")
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])
        attempts = [0]

        def _ready_fn(molecule: str) -> List[dict]:
            # Return the bead only on the first look so a V-fail (which leaves it
            # open) does not spin forever; the loop then drains to queue-empty.
            attempts[0] += 1
            if attempts[0] <= 1 and tracker.get_status(bead["id"]) == "open":
                return [bead]
            return []

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            verify_exit=1,  # V fails — a real failure, not a rate-limit
            dispatch_fn=lambda p, m, t: {"tokens": 5, "output": "did some work"},
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=1, max_iterations=10)

        summary = run_mayor_loop(cfg, runners)

        assert summary.rate_limited is False
        assert summary.rate_limited_beads == 0
        assert summary.stop_reason != "rate-limited"
        assert summary.failed >= 1
        assert bead["id"] not in tracker.closed_beads


# ---------------------------------------------------------------------------
# 8 — clean resume
# ---------------------------------------------------------------------------

class TestCleanResume:
    def test_paused_bead_redispatches_and_closes_on_resume(
        self, tmp_path: Path
    ) -> None:
        """First run rate-limits (pause); a second run (healthy) closes the bead."""
        bead = _make_bead("fblai-resume-1")
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        def _ready_fn(molecule: str) -> List[dict]:
            return [bead] if tracker.get_status(bead["id"]) == "open" else []

        # --- Run 1: rate-limited dispatch → pause, bead returned to open ---
        rl_runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            dispatch_fn=lambda p, m, t: {"tokens": 0, "output": "429 rate limit"},
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2, max_iterations=20)
        s1 = run_mayor_loop(cfg, rl_runners)

        assert s1.stop_reason == "rate-limited"
        assert s1.closed == 0
        assert tracker.get_status(bead["id"]) == "open"  # not burned

        # --- Run 2 (resume): healthy dispatch + V pass → bead closes ---
        ok_runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            verify_exit=0,
            dispatch_fn=lambda p, m, t: {"tokens": 7, "output": "done"},
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
        )
        s2 = run_mayor_loop(cfg, ok_runners)

        assert s2.closed == 1
        assert s2.stop_reason == "queue-empty"
        assert bead["id"] in tracker.closed_beads


# ---------------------------------------------------------------------------
# 9 — worktree teardown discards partial code on rate-limit (VA0b lifecycle)
# ---------------------------------------------------------------------------

class TestWorktreeTeardownOnRateLimit:
    def test_partial_worktree_torn_down_and_not_merged(self, tmp_path: Path) -> None:
        bead = _make_bead("fblai-rl-wt")
        tracker = _StatusTracker()
        tracker.set_open(bead["id"])

        teardowns: list[tuple] = []
        merges: list[str] = []

        def _ready_fn(molecule: str) -> List[dict]:
            return [bead] if tracker.get_status(bead["id"]) == "open" else []

        def _worktree_create(bead_id: str):
            return (str(tmp_path / bead_id), f"mayor/{bead_id}")

        def _worktree_teardown(path: str, branch: str) -> None:
            teardowns.append((path, branch))

        def _merge_branch(branch: str) -> int:
            merges.append(branch)
            return 0

        def _dispatch_with_cwd(prompt, model, timeout_s, cwd):
            return {"tokens": 0, "output": "Claude AI usage limit reached"}

        runners = _make_mayor_runners(
            ready_beads_fn=_ready_fn,
            tracker=tracker,
            loop_state_path=tmp_path / "loop-state.json",
            worktree_create=_worktree_create,
            worktree_teardown=_worktree_teardown,
            merge_branch=_merge_branch,
            run_verify_in_cwd=lambda c, t, cwd: 0,
            dispatch_with_cwd=_dispatch_with_cwd,
        )
        cfg = _make_cfg(max_workers=1, max_iterations=10)

        summary = run_mayor_loop(cfg, runners)

        assert summary.stop_reason == "rate-limited"
        assert summary.rate_limited_beads == 1
        # Partial code discarded (worktree torn down), never merged.
        assert len(teardowns) == 1
        assert teardowns[0][1] == "mayor/fblai-rl-wt"
        assert merges == [], "rate-limited code must NOT be merged"
        assert bead["id"] not in tracker.closed_beads
        assert tracker.get_status(bead["id"]) == "open"


# ---------------------------------------------------------------------------
# 10 — live dispatch surfaces a structured rate_limited flag
# ---------------------------------------------------------------------------

class TestLiveDispatchSignal:
    def test_live_dispatch_sets_rate_limited_from_cli_error_json(self) -> None:
        import json as _json
        from unittest.mock import patch
        import loop_runner

        cli_json = _json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Claude AI usage limit reached",
                "usage": {"output_tokens": 0},
            }
        )
        fake = MagicMock(stdout=cli_json, stderr="", returncode=1)
        with patch.object(loop_runner.subprocess, "run", return_value=fake):
            out = loop_runner._live_dispatch_with_cwd("p", "sonnet", 30, cwd=None)
        assert out["rate_limited"] is True

    def test_live_dispatch_clean_success_is_not_rate_limited(self) -> None:
        import json as _json
        from unittest.mock import patch
        import loop_runner

        cli_json = _json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "all done",
                "usage": {"output_tokens": 12},
            }
        )
        fake = MagicMock(stdout=cli_json, stderr="", returncode=0)
        with patch.object(loop_runner.subprocess, "run", return_value=fake):
            out = loop_runner._live_dispatch_with_cwd("p", "sonnet", 30, cwd=None)
        assert out["rate_limited"] is False
        assert out["tokens"] == 12


# ---------------------------------------------------------------------------
# 11 — a rate-limit pause is a clean (exit 0) outcome in main()
# ---------------------------------------------------------------------------

class TestMainCleanExit:
    def test_main_returns_0_on_rate_limited_stop(self, monkeypatch) -> None:
        import loop_runner

        def _fake_run_mayor_loop(cfg, runners):
            return MayorSummary(stop_reason="rate-limited", rate_limited=True,
                                rate_limited_beads=1)

        monkeypatch.setattr(loop_runner, "run_mayor_loop", _fake_run_mayor_loop)

        rc = loop_runner.main([
            "--molecule", "epic:mayor-v2",
            "--verify-cmd", "true",
            "--max-workers", "3",
        ])
        assert rc == 0, "a rate-limit pause must exit 0 (clean, resumable)"
