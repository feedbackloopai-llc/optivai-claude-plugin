"""test_judge.py — P2.2 unit tests for reconcile (AI-judge seam + Mayor integration).

Test plan from docs/plans/2026-06-21-mayor-p2-reconciler.md §P2.2:
  1. fake judge returning 'kill'   → ReconcileAction(decision='kill') returned
  2. fake judge returning 'respawn' → ReconcileAction(decision='respawn') returned
  3. fake judge returning 'wait'    → ReconcileAction(decision='wait') returned
  4. judge raises/None              → fail-safe to 'wait' (no auto-kill)
  5. respawn-cap enforced           → second respawn denied, demoted to 'wait'
  6. judge NOT called for guarded-out candidates → assert call count
  7. single-writer preserved in run_mayor_loop with reconcile wired in

Run: python3 -m pytest scripts/tests/test_judge.py -q
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set
from unittest.mock import MagicMock, call

import pytest

# Ensure scripts/ is on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    RunConfig,
    Runners,
    MayorSummary,
    WorkerHandle,
    WorkerResult,
    run_mayor_loop,
)
from reconciler import (
    StuckCandidate,
    ReconcileAction,
    detect_candidates,
    survives_guards,
    reconcile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _done_future(result=None) -> concurrent.futures.Future:
    f: concurrent.futures.Future = concurrent.futures.Future()
    f.set_result(result)
    return f


def _pending_future() -> concurrent.futures.Future:
    return concurrent.futures.Future()


def _handle(bead_id: str, started_at: float, done: bool = False) -> WorkerHandle:
    future = _done_future() if done else _pending_future()
    return WorkerHandle(bead_id=bead_id, future=future, model="sonnet", started_at=started_at)


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
        stuck_threshold_s=1800.0,
        spawning_window_s=300.0,
        max_respawns=1,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def _all_clear_reconcile_args(
    bead_id: str = "fblai-stuck",
    kind: str = "crashed",
    recovery_blocked: Optional[Set[str]] = None,
) -> dict:
    """Build minimal reconcile args for a single candidate that passes all guards."""
    rb = recovery_blocked if recovery_blocked is not None else {bead_id}
    return dict(
        active={},
        recovery_blocked=rb,
        bead_statuses={bead_id: "in_progress"},
        respawn_counts={},
        cfg_stuck_threshold_s=1800.0,
        cfg_spawning_window_s=300.0,
        cfg_max_respawns=1,
        now=9999.0,
    )


# ---------------------------------------------------------------------------
# Test 1 — judge returning 'kill' produces kill action
# ---------------------------------------------------------------------------

class TestJudgeKill:
    def test_kill_action_returned(self) -> None:
        """Fake judge returning 'kill' → reconcile yields ReconcileAction(decision='kill')."""
        judge = MagicMock(return_value="kill")

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-kill"),
            judge=judge,
        )

        assert len(actions) == 1
        assert actions[0].bead_id == "fblai-kill"
        assert actions[0].decision == "kill"
        assert judge.call_count == 1

    def test_kill_candidate_arg_is_stuck_candidate(self) -> None:
        """The judge receives a StuckCandidate as its first arg."""
        received: list = []

        def _judge(c: StuckCandidate, ctx: dict) -> str:
            received.append(c)
            return "kill"

        reconcile(**_all_clear_reconcile_args("fblai-kill2"), judge=_judge)

        assert len(received) == 1
        assert isinstance(received[0], StuckCandidate)
        assert received[0].bead_id == "fblai-kill2"


# ---------------------------------------------------------------------------
# Test 2 — judge returning 'respawn' produces respawn action
# ---------------------------------------------------------------------------

class TestJudgeRespawn:
    def test_respawn_action_returned(self) -> None:
        """Fake judge returning 'respawn' → ReconcileAction(decision='respawn')."""
        judge = MagicMock(return_value="respawn")

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-respawn"),
            judge=judge,
        )

        assert len(actions) == 1
        assert actions[0].decision == "respawn"


# ---------------------------------------------------------------------------
# Test 3 — judge returning 'wait' produces wait action
# ---------------------------------------------------------------------------

class TestJudgeWait:
    def test_wait_action_returned(self) -> None:
        """Fake judge returning 'wait' → ReconcileAction(decision='wait')."""
        judge = MagicMock(return_value="wait")

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-wait"),
            judge=judge,
        )

        assert len(actions) == 1
        assert actions[0].decision == "wait"


# ---------------------------------------------------------------------------
# Test 4 — judge raises / returns None → fail-safe to 'wait'
# ---------------------------------------------------------------------------

class TestJudgeFailSafe:
    def test_judge_raises_falls_back_to_wait(self) -> None:
        """If the judge raises an exception, the action is 'wait' (never auto-kill)."""
        def _raising_judge(c: StuckCandidate, ctx: dict) -> str:
            raise RuntimeError("judge exploded")

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-raise"),
            judge=_raising_judge,
        )

        assert len(actions) == 1
        assert actions[0].decision == "wait"

    def test_judge_returns_none_falls_back_to_wait(self) -> None:
        """If the judge returns None, the action is 'wait'."""
        judge = MagicMock(return_value=None)

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-none"),
            judge=judge,
        )

        assert len(actions) == 1
        assert actions[0].decision == "wait"

    def test_judge_returns_invalid_value_falls_back_to_wait(self) -> None:
        """If the judge returns an unrecognized string, action is 'wait'."""
        judge = MagicMock(return_value="obliterate")

        actions = reconcile(
            **_all_clear_reconcile_args("fblai-invalid"),
            judge=judge,
        )

        assert len(actions) == 1
        assert actions[0].decision == "wait"

    def test_no_judge_registered_falls_back_to_wait(self) -> None:
        """judge=None → all candidates get 'wait' (no AI, no kill)."""
        actions = reconcile(
            **_all_clear_reconcile_args("fblai-nojudge"),
            judge=None,
        )

        assert len(actions) == 1
        assert actions[0].decision == "wait"


# ---------------------------------------------------------------------------
# Test 5 — respawn-cap enforced
# ---------------------------------------------------------------------------

class TestRespawnCap:
    def test_first_respawn_allowed(self) -> None:
        """First respawn decision passes when count is 0 and max_respawns=1."""
        judge = MagicMock(return_value="respawn")
        bead_id = "fblai-resp-cap"

        actions = reconcile(
            active={},
            recovery_blocked={bead_id},
            bead_statuses={bead_id: "in_progress"},
            respawn_counts={bead_id: 0},   # not yet respawned
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=1,
            now=9999.0,
            judge=judge,
        )

        assert actions[0].decision == "respawn"

    def test_second_respawn_denied(self) -> None:
        """Second respawn demoted to 'wait' when respawn_count >= max_respawns."""
        judge = MagicMock(return_value="respawn")
        bead_id = "fblai-resp-cap2"

        actions = reconcile(
            active={},
            recovery_blocked={bead_id},
            bead_statuses={bead_id: "in_progress"},
            respawn_counts={bead_id: 1},   # already respawned once
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=1,
            now=9999.0,
            judge=judge,
        )

        # Judge voted "respawn" but cap prevents it
        assert actions[0].decision == "wait"

    def test_respawn_cap_zero_denies_all_respawns(self) -> None:
        """max_respawns=0 → no respawns ever allowed."""
        judge = MagicMock(return_value="respawn")
        bead_id = "fblai-resp-cap0"

        actions = reconcile(
            active={},
            recovery_blocked={bead_id},
            bead_statuses={bead_id: "in_progress"},
            respawn_counts={bead_id: 0},
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=0,            # zero cap
            now=9999.0,
            judge=judge,
        )

        assert actions[0].decision == "wait"


# ---------------------------------------------------------------------------
# Test 6 — judge NOT called for guarded-out candidates
# ---------------------------------------------------------------------------

class TestJudgeNotCalledForGuardedCandidates:
    def test_judge_not_called_when_no_survivors(self) -> None:
        """If all candidates are guarded out, the judge is never invoked."""
        judge = MagicMock(return_value="kill")
        bead_id = "fblai-guarded"

        # candidate that will be guarded by terminal-state (bead_status='closed')
        actions = reconcile(
            active={},
            recovery_blocked={bead_id},
            bead_statuses={bead_id: "closed"},    # terminal-state guard fires
            respawn_counts={},
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=1,
            now=9999.0,
            judge=judge,
        )

        assert judge.call_count == 0, (
            f"Judge was called {judge.call_count} time(s) for a guarded-out candidate — "
            "should never happen"
        )
        assert actions == []

    def test_judge_call_count_matches_survivors(self) -> None:
        """Judge is called exactly once per guard-surviving candidate."""
        judge = MagicMock(return_value="wait")
        bead_ids = ["fblai-s1", "fblai-s2", "fblai-s3"]

        # s1 and s3 survive guards (in_progress); s2 guarded out (closed)
        actions = reconcile(
            active={},
            recovery_blocked=set(bead_ids),
            bead_statuses={
                "fblai-s1": "in_progress",
                "fblai-s2": "closed",       # guarded out by terminal-state
                "fblai-s3": "in_progress",
            },
            respawn_counts={},
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=1,
            now=9999.0,
            judge=judge,
        )

        # Judge called only for s1 and s3
        assert judge.call_count == 2, (
            f"Judge call count: expected 2, got {judge.call_count}"
        )
        action_ids = {a.bead_id for a in actions}
        assert "fblai-s2" not in action_ids
        assert "fblai-s1" in action_ids
        assert "fblai-s3" in action_ids

    def test_empty_recovery_blocked_no_candidates(self) -> None:
        """No crashed beads and no hung beads → no calls to judge."""
        judge = MagicMock(return_value="kill")

        actions = reconcile(
            active={},
            recovery_blocked=set(),
            bead_statuses={},
            respawn_counts={},
            cfg_stuck_threshold_s=1800.0,
            cfg_spawning_window_s=300.0,
            cfg_max_respawns=1,
            now=9999.0,
            judge=judge,
        )

        assert judge.call_count == 0
        assert actions == []


# ---------------------------------------------------------------------------
# Test 7 — run_mayor_loop integration: reconcile wired in, single-writer preserved
# ---------------------------------------------------------------------------

class TestReconcileIntegration:
    """Integration test: run_mayor_loop with reconcile wired into the tick.

    Verifies the single-writer invariant by checking that all bead mutations
    come from the main Mayor thread, not from worker threads.
    """

    def test_single_writer_with_reconcile(self, tmp_path: Path) -> None:
        """The Mayor (main thread) applies all mutations from reconcile actions.
        No worker thread should ever call beads_close or beads_update.
        """
        bead = {
            "id": "fblai-sw-reconcile",
            "title": "Integration test bead",
            "priority": 1,
            "labels": [],
            "body": "",
        }

        closed_threads: list = []
        updated_threads: list = []
        close_lock = threading.Lock()
        update_lock = threading.Lock()
        tracker_statuses: dict = {"fblai-sw-reconcile": "open"}

        def _close(bead_id: str) -> None:
            with close_lock:
                tracker_statuses[bead_id] = "closed"
                closed_threads.append(threading.current_thread().name)

        def _update(bead_id: str, status: str) -> None:
            with update_lock:
                tracker_statuses[bead_id] = status
                updated_threads.append(threading.current_thread().name)

        def _ready_fn(molecule: str) -> list:
            if tracker_statuses.get("fblai-sw-reconcile") == "open":
                return [bead]
            return []

        runners = Runners(
            beads_ready=_ready_fn,
            beads_close=_close,
            beads_update=_update,
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            loop_state_path=tmp_path / "loop-state.json",
            judge=MagicMock(return_value="wait"),   # reconcile wired but judge says wait
        )

        cfg = _make_cfg(max_workers=1, max_iterations=5, stuck_threshold_s=1800.0)
        summary = run_mayor_loop(cfg, runners)

        # The bead should have been closed (verify_exit=0)
        assert summary.closed == 1
        assert tracker_statuses["fblai-sw-reconcile"] == "closed"

        # All closes must come from the main thread (Mayor), not a worker
        for thread_name in closed_threads:
            assert "ThreadPoolExecutor" not in thread_name, (
                f"beads_close called from worker thread '{thread_name}' — "
                "single-writer invariant violated"
            )

        # All updates must also come from the main thread
        for thread_name in updated_threads:
            assert "ThreadPoolExecutor" not in thread_name, (
                f"beads_update called from worker thread '{thread_name}' — "
                "single-writer invariant violated"
            )
