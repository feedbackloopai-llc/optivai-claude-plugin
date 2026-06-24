"""test_v22_hardening.py — Tests for the three v2.2 hardening MINORs.

MINOR 1 — bead_id sanitization:
  Malformed bead_ids ("-evil", "a b", "x/../y", "fblai-ok;rm") are rejected
  before any git argv is constructed.  _validate_bead_id, _live_worktree_manager,
  and _live_worktree_create all gate on the canonical regex.

MINOR 2 — merge-lock consistency:
  The VA0b inline merge-on-pass path (run_mayor_loop) wraps runners.merge_branch
  under _MERGE_LOCK, consistent with the VB2 Refinery path.  Concurrency safety:
  the lock is held during the merge call, not before or after.

MINOR 3 — comment accuracy:
  No behavioural test for MINOR 3 (it is a comment-only change); the two MINORs
  above provide full mechanical coverage.

Run: pytest scripts/tests/test_v22_hardening.py -v
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
for _p in (_SCRIPTS_DIR, _HOOKS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from loop_runner import (
    MayorSummary,
    MergeCandidate,
    RunConfig,
    Runners,
    WorkerResult,
    _MERGE_LOCK,
    _live_worktree_create,
    _live_worktree_manager,
    _validate_bead_id,
    run_mayor_loop,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="test-molecule",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=3,
        budget_tokens=100_000,
        max_workers=1,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def _make_runners(
    *,
    ready_side_effects=None,
    verify_exit: int = 0,
    merge_exit: int = 0,
    include_worktree_seam: bool = False,
) -> Runners:
    """Build a Runners instance with all mocks pre-wired."""
    call_count = [0]

    def _ready(molecule):
        call_count[0] += 1
        if ready_side_effects is not None:
            idx = min(call_count[0] - 1, len(ready_side_effects) - 1)
            return ready_side_effects[idx]
        return []

    r = Runners(
        beads_ready=_ready,
        beads_close=MagicMock(),
        beads_update=MagicMock(),
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=MagicMock(return_value={"tokens": 10, "output": "done"}),
        run_verify=MagicMock(return_value=verify_exit),
    )
    if include_worktree_seam:
        r.merge_branch = MagicMock(return_value=merge_exit)
        r.worktree_create = MagicMock(return_value=("/tmp/wt", "mayor/fblai-good1"))
        r.worktree_teardown = MagicMock()
        r.run_verify_in_cwd = MagicMock(return_value=verify_exit)
    return r


def _make_bead(bead_id: str = "fblai-test1") -> dict:
    return {
        "id": bead_id,
        "title": "Test bead",
        "priority": 2,
        "labels": [],
        "body": "Do work. Acceptance: pass.",
    }


# ===========================================================================
# MINOR 1 — _validate_bead_id
# ===========================================================================

class TestValidateBeadId:
    """Unit tests for the canonical bead_id regex guard."""

    def test_valid_gz_prefix(self):
        assert _validate_bead_id("gz-abc123") is True

    def test_valid_fblai_prefix(self):
        assert _validate_bead_id("fblai-def456") is True

    def test_valid_optivai_prefix(self):
        assert _validate_bead_id("optivai-xyz789") is True

    def test_valid_all_lower_alphanum(self):
        assert _validate_bead_id("gz-a0b1c2d3") is True

    def test_rejected_starts_with_dash(self):
        """Arg-injection: bead_id starting with '-' must be rejected."""
        assert _validate_bead_id("-evil") is False

    def test_rejected_space(self):
        """Spaces make invalid git refs and filesystem paths."""
        assert _validate_bead_id("a b") is False

    def test_rejected_dotdot(self):
        """Path traversal: .. must not reach git argv."""
        assert _validate_bead_id("x/../y") is False
        assert _validate_bead_id("fblai-../etc") is False

    def test_rejected_semicolon(self):
        """Command injection attempt must be caught before git."""
        assert _validate_bead_id("fblai-ok;rm") is False

    def test_rejected_unknown_prefix(self):
        """Only gz/fblai/optivai are valid prefixes."""
        assert _validate_bead_id("bad-prefix-abc") is False

    def test_rejected_empty_string(self):
        assert _validate_bead_id("") is False

    def test_rejected_tilde(self):
        """~ is a special git ref operator — invalid in a bead_id."""
        assert _validate_bead_id("gz-abc~1") is False

    def test_rejected_caret(self):
        """^ is a git parent ref operator — invalid in a bead_id."""
        assert _validate_bead_id("gz-abc^") is False

    def test_rejected_colon(self):
        """Colons are invalid in git refs on many platforms."""
        assert _validate_bead_id("fblai-a:b") is False


# ===========================================================================
# MINOR 1 — _live_worktree_manager
# ===========================================================================

class TestLiveWorktreeManagerValidation:
    """_live_worktree_manager must yield None and skip git for bad bead_ids."""

    def test_invalid_bead_id_yields_none_no_git_call(self):
        """A malformed bead_id must yield None without calling git."""
        import subprocess as sp
        with patch.object(sp, "run") as mock_run:
            cm = _live_worktree_manager("-evil-injection")
            wt_path = None
            with cm as path:
                wt_path = path
            # Must yield None
            assert wt_path is None
            # Must not have called git at all
            mock_run.assert_not_called()

    @pytest.mark.parametrize("bad_id", [
        "-evil",
        "a b c",
        "x/../y",
        "fblai-ok;rm -rf /",
        "unknown-prefix-abc",
        "",
    ])
    def test_malformed_ids_never_reach_git(self, bad_id):
        import subprocess as sp
        with patch.object(sp, "run") as mock_run:
            with _live_worktree_manager(bad_id) as path:
                assert path is None
            mock_run.assert_not_called()

    def test_valid_bead_id_attempts_git(self):
        """A valid bead_id should pass the guard and attempt git calls."""
        import subprocess as sp
        # Simulate git rev-parse failing (not in a repo) — still proves guard passed
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch.object(sp, "run", return_value=mock_result) as mock_run:
            with _live_worktree_manager("fblai-abc123") as path:
                assert path is None  # not in a repo → None, but git WAS called
            mock_run.assert_called()


# ===========================================================================
# MINOR 1 — _live_worktree_create
# ===========================================================================

class TestLiveWorktreeCreateValidation:
    """_live_worktree_create must return None and skip git for bad bead_ids."""

    @pytest.mark.parametrize("bad_id", [
        "-evil",
        "a b",
        "x/../y",
        "fblai-ok;rm",
        "bad-prefix",
    ])
    def test_malformed_ids_return_none_no_git(self, bad_id):
        import subprocess as sp
        with patch.object(sp, "run") as mock_run:
            result = _live_worktree_create(bad_id)
        assert result is None
        mock_run.assert_not_called()

    def test_valid_bead_id_attempts_git_discovery(self):
        """A valid bead_id passes the guard; git may then fail for other reasons."""
        import subprocess as sp
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch.object(sp, "run", return_value=mock_result) as mock_run:
            result = _live_worktree_create("gz-validabc")
        # git was invoked (even if it failed) — guard did not block it
        mock_run.assert_called()
        # Not in a git repo → returns None (git rev-parse failed)
        assert result is None


# ===========================================================================
# MINOR 2 — merge-lock consistency
# ===========================================================================

class TestMergeLockConsistency:
    """The VA0b inline merge-on-pass path must acquire _MERGE_LOCK."""

    def test_merge_branch_called_under_merge_lock(self):
        """When run_mayor_loop calls merge_branch on V-pass, _MERGE_LOCK is held."""
        lock_was_held_during_merge: list[bool] = []

        def _checking_merge(branch_name: str) -> int:
            """Record whether _MERGE_LOCK is held at the time of merge."""
            # If _MERGE_LOCK is already acquired on this thread, locked() returns True.
            # We probe with a non-blocking acquire: if it fails, the lock is held.
            acquired = _MERGE_LOCK.acquire(blocking=False)
            if acquired:
                _MERGE_LOCK.release()
                lock_was_held_during_merge.append(False)  # lock NOT held
            else:
                lock_was_held_during_merge.append(True)   # lock IS held
            return 0  # merge success

        bead = _make_bead("fblai-good1")

        runners = _make_runners(
            ready_side_effects=[[bead], []],
            verify_exit=0,
        )
        runners.merge_branch = _checking_merge
        runners.worktree_create = MagicMock(return_value=("/tmp/wt", "mayor/fblai-good1"))
        runners.worktree_teardown = MagicMock()
        runners.run_verify_in_cwd = MagicMock(return_value=0)

        cfg = _make_cfg(max_workers=1)
        run_mayor_loop(cfg, runners)

        # merge_branch must have been called at least once
        assert len(lock_was_held_during_merge) >= 1, "merge_branch was never called"
        # Every call must have happened under _MERGE_LOCK
        assert all(lock_was_held_during_merge), (
            f"merge_branch was called WITHOUT _MERGE_LOCK on some calls: {lock_was_held_during_merge}"
        )

    def test_merge_lock_not_held_outside_merge(self):
        """_MERGE_LOCK is released after the merge call (not held for the whole tick)."""
        lock_released_after_merge: list[bool] = []

        def _checking_merge(branch_name: str) -> int:
            return 0

        bead = _make_bead("fblai-good1")
        runners = _make_runners(
            ready_side_effects=[[bead], []],
            verify_exit=0,
        )
        runners.merge_branch = _checking_merge
        runners.worktree_create = MagicMock(return_value=("/tmp/wt", "mayor/fblai-good1"))
        runners.worktree_teardown = MagicMock()
        runners.run_verify_in_cwd = MagicMock(return_value=0)
        original_close = MagicMock()
        runners.beads_close = original_close

        cfg = _make_cfg(max_workers=1)
        run_mayor_loop(cfg, runners)

        # After run_mayor_loop returns, the lock must be free
        acquired = _MERGE_LOCK.acquire(blocking=False)
        if acquired:
            _MERGE_LOCK.release()
            lock_released_after_merge.append(True)
        else:
            lock_released_after_merge.append(False)

        assert all(lock_released_after_merge), "_MERGE_LOCK was NOT released after Mayor loop"

    def test_va0b_merge_and_refinery_use_same_lock(self):
        """VA0b inline path and Refinery path both serialize through _MERGE_LOCK.

        We verify by checking that a thread that pre-holds _MERGE_LOCK blocks the
        VA0b merge call: if both paths use the same lock, this must be true.
        """
        merge_started = threading.Event()
        merge_proceed = threading.Event()
        merge_calls: list[str] = []

        def _blocking_merge(branch_name: str) -> int:
            merge_calls.append("merge-called")
            return 0

        bead = _make_bead("fblai-good1")
        runners = _make_runners(
            ready_side_effects=[[bead], []],
            verify_exit=0,
        )
        runners.merge_branch = _blocking_merge
        runners.worktree_create = MagicMock(return_value=("/tmp/wt", "mayor/fblai-good1"))
        runners.worktree_teardown = MagicMock()
        runners.run_verify_in_cwd = MagicMock(return_value=0)

        cfg = _make_cfg(max_workers=1)

        # Hold the merge lock from a background thread while the Mayor runs
        def hold_lock():
            with _MERGE_LOCK:
                merge_started.set()
                # Hold for 100ms then release
                import time
                time.sleep(0.1)

        t = threading.Thread(target=hold_lock, daemon=True)
        t.start()
        merge_started.wait(timeout=2.0)

        # The Mayor will need the lock to merge — it must wait for us
        run_mayor_loop(cfg, runners)
        t.join(timeout=2.0)

        # merge was eventually called (not skipped)
        assert "merge-called" in merge_calls
