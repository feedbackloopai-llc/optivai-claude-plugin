"""test_ledger.py — Tests for the Mayor-side brain ledger (P3.2).

Coverage:
  - brain_capture fires for dispatch, verify-pass, verify-fail, and close transitions.
  - Each ledger call includes the bead_id, molecule, action, and tier.
  - Fail-safe: brain_capture raising does NOT crash the loop.
  - Ledger captures are separate from worker-side gate-block/dispatch-exception captures.

Run: python3 -m pytest scripts/tests/test_ledger.py -q
"""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, call, patch

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
    MayorSummary,
    WorkerResult,
    _ledger_capture,
    run_mayor_loop,
    LOOP_BUDGET_TOKENS,
    LOOP_MAX_ITERATIONS,
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


def _make_cfg(*, max_workers: int = 2, **overrides) -> RunConfig:
    defaults = dict(
        molecule="epic:ledger-test",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=LOOP_MAX_ITERATIONS,
        budget_tokens=LOOP_BUDGET_TOKENS,
        dry_run=False,
        once=False,
        max_workers=max_workers,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def _make_runners(
    *,
    ready_beads: list | None = None,
    verify_exit: int = 0,
    loop_state_path: Optional[Path] = None,
    brain_capture_side_effect=None,
) -> Runners:
    """Build a Runners instance for Mayor ledger tests."""
    brain_capture_mock = MagicMock(side_effect=brain_capture_side_effect)
    runners = Runners(
        beads_ready=MagicMock(return_value=ready_beads if ready_beads is not None else []),
        beads_close=MagicMock(),
        beads_update=MagicMock(),
        brain_recall=MagicMock(return_value=""),
        brain_capture=brain_capture_mock,
        dispatch=MagicMock(return_value={"tokens": 10, "output": "done"}),
        run_verify=MagicMock(return_value=verify_exit),
        loop_state_path=loop_state_path,
    )
    return runners


# ---------------------------------------------------------------------------
# _ledger_capture unit tests
# ---------------------------------------------------------------------------

class TestLedgerCaptureUnit:
    """Direct unit tests for _ledger_capture helper."""

    def _make_minimal_runners(self, capture_mock=None) -> Runners:
        mock = capture_mock or MagicMock()
        return Runners(
            beads_ready=MagicMock(return_value=[]),
            beads_close=MagicMock(),
            brain_recall=MagicMock(return_value=""),
            brain_capture=mock,
            dispatch=MagicMock(return_value={"tokens": 0, "output": ""}),
            run_verify=MagicMock(return_value=0),
        )

    def test_capture_called_with_correct_fields(self) -> None:
        """_ledger_capture calls brain_capture with bead_id, molecule, action, tier."""
        capture_mock = MagicMock()
        runners = self._make_minimal_runners(capture_mock)

        _ledger_capture(
            runners,
            action="dispatch",
            bead_id="fblai-xyz",
            molecule="epic:test",
            tier="sonnet",
        )

        capture_mock.assert_called_once()
        text_arg, type_arg = capture_mock.call_args[0]
        assert "dispatch" in text_arg
        assert "fblai-xyz" in text_arg
        assert "epic:test" in text_arg
        assert "sonnet" in text_arg
        assert type_arg == "decision"

    def test_extra_included_in_text(self) -> None:
        """_ledger_capture includes the extra string in the captured text."""
        capture_mock = MagicMock()
        runners = self._make_minimal_runners(capture_mock)

        _ledger_capture(
            runners,
            action="verify-fail",
            bead_id="fblai-abc",
            molecule="epic:test",
            tier="haiku",
            extra="verify_exit=1",
        )

        text_arg = capture_mock.call_args[0][0]
        assert "verify_exit=1" in text_arg

    def test_fail_safe_on_brain_capture_raise(self) -> None:
        """_ledger_capture must NOT raise when brain_capture throws."""
        capture_mock = MagicMock(side_effect=RuntimeError("brain unavailable"))
        runners = self._make_minimal_runners(capture_mock)

        # Must not raise — fail-safe contract
        _ledger_capture(
            runners,
            action="close",
            bead_id="fblai-safe",
            molecule="epic:test",
            tier="opus",
        )

    def test_unknown_tier_does_not_raise(self) -> None:
        """_ledger_capture handles tier=None gracefully."""
        capture_mock = MagicMock()
        runners = self._make_minimal_runners(capture_mock)

        _ledger_capture(
            runners,
            action="dispatch",
            bead_id="fblai-notier",
            molecule="epic:test",
            tier=None,
        )

        capture_mock.assert_called_once()
        text_arg = capture_mock.call_args[0][0]
        assert "unknown" in text_arg  # tier=None → "unknown"


# ---------------------------------------------------------------------------
# run_mayor_loop ledger integration tests
# ---------------------------------------------------------------------------

class TestMayorLoopLedger:
    """Integration tests: run_mayor_loop emits the right ledger events."""

    def _collect_ledger_calls(self, runners: Runners) -> List[tuple]:
        """Return all (text, type_) pairs captured by brain_capture."""
        return [c[0] for c in runners.brain_capture.call_args_list]

    def _filter_ledger(self, calls: List[tuple]) -> List[tuple]:
        """Return only Mayor ledger calls (text contains 'Mayor ledger')."""
        return [(t, typ) for t, typ in calls if "Mayor ledger" in t]

    def test_dispatch_ledger_emitted(self, tmp_path: Path) -> None:
        """A 'dispatch' ledger event is emitted for each bead dispatched."""
        bead = _make_bead("fblai-d1", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2)

        run_mayor_loop(cfg, runners)

        ledger = self._filter_ledger(self._collect_ledger_calls(runners))
        dispatch_events = [(t, typ) for t, typ in ledger if "action=dispatch" in t]
        assert len(dispatch_events) >= 1
        text, type_ = dispatch_events[0]
        assert "fblai-d1" in text
        assert "epic:ledger-test" in text
        assert type_ == "decision"

    def test_verify_pass_and_close_ledger_emitted(self, tmp_path: Path) -> None:
        """When V passes, both 'verify-pass' and 'close' ledger events are emitted."""
        bead = _make_bead("fblai-vc", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2)

        run_mayor_loop(cfg, runners)

        ledger = self._filter_ledger(self._collect_ledger_calls(runners))
        actions = {t.split("action=")[1].split()[0] for t, _ in ledger if "action=" in t}
        assert "verify-pass" in actions
        assert "close" in actions

    def test_verify_fail_ledger_emitted(self, tmp_path: Path) -> None:
        """When V fails, a 'verify-fail' ledger event is emitted."""
        bead = _make_bead("fblai-vf", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=1,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2)

        run_mayor_loop(cfg, runners)

        ledger = self._filter_ledger(self._collect_ledger_calls(runners))
        fail_events = [(t, typ) for t, typ in ledger if "action=verify-fail" in t]
        assert len(fail_events) >= 1

    def test_fail_safe_brain_unavailable_does_not_crash_loop(self, tmp_path: Path) -> None:
        """Loop completes normally even when brain_capture always raises."""
        bead = _make_bead("fblai-safe", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
            brain_capture_side_effect=RuntimeError("brain down"),
        )
        cfg = _make_cfg(max_workers=2)

        # Must complete without raising
        summary = run_mayor_loop(cfg, runners)
        # The bead may or may not close (brain down prevents captures but not close),
        # but the loop must have returned a MayorSummary with a stop_reason
        assert summary.stop_reason  # any non-empty stop reason is fine

    def test_ledger_events_contain_molecule(self, tmp_path: Path) -> None:
        """All ledger events include the molecule name."""
        bead = _make_bead("fblai-mol", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2, molecule="epic:mol-check")

        run_mayor_loop(cfg, runners)

        ledger = self._filter_ledger(self._collect_ledger_calls(runners))
        for text, _ in ledger:
            assert "epic:mol-check" in text, f"molecule missing from ledger event: {text}"

    def test_no_duplicate_gate_block_captures_in_ledger(self, tmp_path: Path) -> None:
        """Mayor ledger events do not duplicate the worker's gate-block/dispatch-exception captures."""
        bead = _make_bead("fblai-nodup", priority=1)
        runners = _make_runners(
            ready_beads=[bead], verify_exit=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        cfg = _make_cfg(max_workers=2)

        run_mayor_loop(cfg, runners)

        all_calls = self._collect_ledger_calls(runners)
        # Worker-side gate-block captures contain "gate-blocked" or "dispatch exception"
        gate_texts = [t for t, _ in all_calls if "gate-blocked" in t or "dispatch exception" in t]
        # Mayor ledger: these SHOULD NOT appear from the ledger (no normal dispatch triggers them)
        # In a normal run with compliant beads, there should be zero gate-block captures
        assert len(gate_texts) == 0, (
            f"Unexpected gate-block captures in normal run: {gate_texts}"
        )
