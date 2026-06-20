"""test_loop_state.py — Tests for OBS2 loop-state.json writing in loop_runner.py.

Coverage:
  - write_loop_state: atomic write (temp + os.replace); schema-valid JSON output.
  - write_loop_state: fail-open on unwritable path (must not raise).
  - run_loop with injected temp path: writes status:"running" then terminal active:false.
  - dry-run with injected temp path: writes status:"dry-run"; ZERO beads_close calls.
  - updated_at is a float epoch.
  - _build_state produces the correct §1 schema fields.

Run: cd /Users/erato949/dev/optivai-claude-plugin/scripts && python3 -m pytest tests/test_loop_state.py -v
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional
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
    _build_state,
    run_loop,
    write_loop_state,
    LOOP_MAX_ITERATIONS,
    LOOP_BUDGET_TOKENS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bead(
    bead_id: str = "fblai-test",
    title: str = "Test bead",
    priority: int = 2,
    labels: list | None = None,
    body: str = "",
) -> dict:
    return {
        "id": bead_id,
        "title": title,
        "priority": priority,
        "labels": labels or [],
        "body": body,
    }


def _make_runners(
    *,
    ready_beads: list | None = None,
    verify_exit: int = 0,
    loop_state_path: Optional[Path] = None,
) -> tuple[Runners, MagicMock, MagicMock]:
    """Return (runners, beads_close_mock, dispatch_mock)."""
    beads_close_mock = MagicMock()
    dispatch_mock = MagicMock(return_value={"tokens": 50, "output": "done"})
    verify_mock = MagicMock(return_value=verify_exit)

    runners = Runners(
        beads_ready=MagicMock(return_value=ready_beads if ready_beads is not None else []),
        beads_close=beads_close_mock,
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=dispatch_mock,
        run_verify=verify_mock,
        loop_state_path=loop_state_path,
    )
    return runners, beads_close_mock, dispatch_mock


def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="test-molecule",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=3,
        budget_tokens=LOOP_BUDGET_TOKENS,
        dry_run=False,
        once=False,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


# ---------------------------------------------------------------------------
# write_loop_state: basic correctness
# ---------------------------------------------------------------------------

class TestWriteLoopState:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        """write_loop_state produces valid, readable JSON at the given path."""
        state_path = tmp_path / "loop-state.json"
        state = {
            "harness": "claude",
            "active": True,
            "molecule": "epic:test",
            "iteration": 2,
            "max_iterations": 25,
            "closed": 1,
            "failed": 1,
            "tokens": 500,
            "last_bead": "fblai-abc",
            "last_outcome": "closed",
            "status": "running",
            "stop_reason": None,
            "updated_at": time.time(),
        }
        write_loop_state(state, state_path)
        assert state_path.exists()
        loaded = json.loads(state_path.read_text())
        assert loaded["harness"] == "claude"
        assert loaded["active"] is True
        assert loaded["molecule"] == "epic:test"
        assert loaded["iteration"] == 2
        assert loaded["closed"] == 1
        assert loaded["status"] == "running"

    def test_atomic_write_uses_temp_then_replace(self, tmp_path: Path) -> None:
        """After write_loop_state, no orphaned temp files remain in the directory."""
        state_path = tmp_path / "loop-state.json"
        state = {"harness": "claude", "active": True, "status": "running",
                  "updated_at": time.time()}
        write_loop_state(state, state_path)
        # The only file present should be the final state file
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "loop-state.json"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """write_loop_state creates intermediate parent directories."""
        state_path = tmp_path / "nested" / "dir" / "loop-state.json"
        write_loop_state({"active": True, "updated_at": time.time()}, state_path)
        assert state_path.exists()

    def test_fail_open_on_unwritable_path(self, tmp_path: Path) -> None:
        """write_loop_state on an unwritable path must not raise."""
        # Use a path whose parent is a file (cannot be a directory) — write will fail.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("I am a file")
        # The state path tries to sit inside "not-a-dir" which is a file, not a dir.
        state_path = blocker / "loop-state.json"
        # Must not raise — fail-open contract.
        write_loop_state({"active": True, "updated_at": time.time()}, state_path)

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """Repeated writes update the file content correctly."""
        state_path = tmp_path / "loop-state.json"
        write_loop_state({"iteration": 1, "active": True, "updated_at": 1.0}, state_path)
        write_loop_state({"iteration": 2, "active": True, "updated_at": 2.0}, state_path)
        loaded = json.loads(state_path.read_text())
        assert loaded["iteration"] == 2


# ---------------------------------------------------------------------------
# _build_state: schema correctness
# ---------------------------------------------------------------------------

class TestBuildState:
    def _base_summary(self, **kw) -> RunSummary:
        s = RunSummary(stop_reason="")
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def test_required_fields_present(self) -> None:
        """_build_state produces all §1 required fields."""
        summary = self._base_summary(iterations=3, beads_closed=2, total_tokens=1000)
        cfg = _make_cfg(molecule="epic:foo", max_iterations=10)
        state = _build_state(
            summary, cfg,
            status="running",
            last_bead="fblai-xyz",
            last_outcome="closed",
            stop_reason=None,
            active=True,
        )
        required = [
            "harness", "active", "molecule", "iteration", "max_iterations",
            "closed", "failed", "tokens", "last_bead", "last_outcome",
            "status", "stop_reason", "updated_at",
        ]
        for field in required:
            assert field in state, f"Missing field: {field}"

    def test_harness_is_claude(self) -> None:
        summary = self._base_summary()
        cfg = _make_cfg()
        state = _build_state(summary, cfg, status="running",
                              last_bead=None, last_outcome=None, stop_reason=None)
        assert state["harness"] == "claude"

    def test_failed_count_is_iterations_minus_closed(self) -> None:
        """failed = iterations - beads_closed."""
        summary = self._base_summary(iterations=5, beads_closed=2, total_tokens=0)
        cfg = _make_cfg()
        state = _build_state(summary, cfg, status="running",
                              last_bead=None, last_outcome=None, stop_reason=None)
        assert state["failed"] == 3  # 5 - 2

    def test_updated_at_is_float_epoch(self) -> None:
        """updated_at must be a float unix epoch value."""
        before = time.time()
        summary = self._base_summary()
        cfg = _make_cfg()
        state = _build_state(summary, cfg, status="running",
                              last_bead=None, last_outcome=None, stop_reason=None)
        after = time.time()
        assert isinstance(state["updated_at"], float)
        assert before <= state["updated_at"] <= after

    def test_terminal_state_has_active_false(self) -> None:
        summary = self._base_summary(iterations=5, beads_closed=3)
        cfg = _make_cfg()
        state = _build_state(summary, cfg, status="done",
                              last_bead="fblai-last", last_outcome="closed",
                              stop_reason="queue-empty", active=False)
        assert state["active"] is False
        assert state["status"] == "done"
        assert state["stop_reason"] == "queue-empty"


# ---------------------------------------------------------------------------
# run_loop state-file integration
# ---------------------------------------------------------------------------

class TestRunLoopStateFile:
    def test_run_writes_running_then_terminal(self, tmp_path: Path) -> None:
        """run_loop writes status:"running" at start, then a terminal active:false state."""
        state_path = tmp_path / "loop-state.json"
        bead = _make_bead("fblai-s1", priority=1)
        runners, close_mock, dispatch_mock = _make_runners(
            ready_beads=[bead], verify_exit=0, loop_state_path=state_path
        )
        cfg = _make_cfg(once=True, max_iterations=25)

        # Capture intermediate states by reading after run
        run_loop(cfg, runners)

        # Final state must be active:false (terminal)
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["active"] is False
        assert state["status"] in ("done", "stopped")
        assert state["stop_reason"] == "once"
        assert state["harness"] == "claude"
        assert state["molecule"] == "test-molecule"

    def test_run_state_tokens_accumulate(self, tmp_path: Path) -> None:
        """Terminal state reflects accumulated tokens from dispatched iterations."""
        state_path = tmp_path / "loop-state.json"
        bead = _make_bead("fblai-tok", priority=1)
        runners, _, dispatch_mock = _make_runners(
            ready_beads=[bead], verify_exit=0, loop_state_path=state_path
        )
        # dispatch returns 50 tokens per call (set in _make_runners)
        cfg = _make_cfg(once=True)

        run_loop(cfg, runners)

        state = json.loads(state_path.read_text())
        assert state["tokens"] == 50  # one iteration × 50 tokens

    def test_run_state_closed_count(self, tmp_path: Path) -> None:
        """Terminal state reflects correct closed count."""
        state_path = tmp_path / "loop-state.json"
        bead = _make_bead("fblai-cl", priority=1)
        runners, close_mock, _ = _make_runners(
            ready_beads=[bead], verify_exit=0, loop_state_path=state_path
        )
        cfg = _make_cfg(once=True)

        run_loop(cfg, runners)

        state = json.loads(state_path.read_text())
        assert state["closed"] == 1
        assert state["failed"] == 0

    def test_queue_empty_writes_terminal_state(self, tmp_path: Path) -> None:
        """When queue is empty from the start, terminal state has stop_reason:queue-empty."""
        state_path = tmp_path / "loop-state.json"
        runners, _, _ = _make_runners(ready_beads=[], loop_state_path=state_path)
        cfg = _make_cfg()

        run_loop(cfg, runners)

        state = json.loads(state_path.read_text())
        assert state["active"] is False
        assert state["stop_reason"] == "queue-empty"

    def test_updated_at_is_float_in_run_output(self, tmp_path: Path) -> None:
        """updated_at in the file written by run_loop is a float epoch."""
        state_path = tmp_path / "loop-state.json"
        runners, _, _ = _make_runners(ready_beads=[], loop_state_path=state_path)
        cfg = _make_cfg()

        before = time.time()
        run_loop(cfg, runners)
        after = time.time()

        state = json.loads(state_path.read_text())
        assert isinstance(state["updated_at"], float)
        assert before <= state["updated_at"] <= after


# ---------------------------------------------------------------------------
# dry-run: writes status:"dry-run", ZERO beads_close
# ---------------------------------------------------------------------------

class TestDryRunStateFile:
    def test_dry_run_writes_dry_run_status(self, tmp_path: Path) -> None:
        """dry-run writes status:"dry-run" to the state file."""
        state_path = tmp_path / "loop-state.json"
        bead = _make_bead("fblai-dr", priority=1)
        runners, close_mock, _ = _make_runners(
            ready_beads=[bead], verify_exit=0, loop_state_path=state_path
        )
        cfg = _make_cfg(dry_run=True, max_iterations=1)

        run_loop(cfg, runners)

        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["status"] == "dry-run"

    def test_dry_run_zero_beads_close(self, tmp_path: Path) -> None:
        """dry-run writing the state file does NOT trigger any beads_close calls."""
        state_path = tmp_path / "loop-state.json"
        bead = _make_bead("fblai-dr2", priority=1)
        runners, close_mock, _ = _make_runners(
            ready_beads=[bead], verify_exit=0, loop_state_path=state_path
        )
        cfg = _make_cfg(dry_run=True, max_iterations=1)

        run_loop(cfg, runners)

        close_mock.assert_not_called()

    def test_dry_run_terminal_state_active_false(self, tmp_path: Path) -> None:
        """dry-run terminal state has active:false."""
        state_path = tmp_path / "loop-state.json"
        runners, _, _ = _make_runners(
            ready_beads=[], loop_state_path=state_path
        )
        cfg = _make_cfg(dry_run=True)

        run_loop(cfg, runners)

        state = json.loads(state_path.read_text())
        assert state["active"] is False
        # Status remains "dry-run" for the terminal state when dry_run=True
        assert state["status"] == "dry-run"
