"""test_guards.py — P2.3 unit tests for survives_guards (pure function).

Test plan from docs/plans/2026-06-21-mayor-p2-reconciler.md §P2.3:
  1. terminal-state guard fires  → candidate dropped (bead_status in done/closed)
  2. stale-hook guard fires      → candidate dropped (assigned_worker is None)
  3. spawning-window guard fires → hung candidate with runtime < window is dropped
  4. TOCTOU guard fires          → future_done=True drops the candidate
  5. genuinely-stuck candidate passes all four guards → survives (returns True)
  6. spawning-window applies to hung but NOT to crashed (crashed bypasses guard 3)

Run: python3 -m pytest scripts/tests/test_guards.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from reconciler import StuckCandidate, survives_guards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hung_candidate(runtime_s: float = 2000.0, bead_id: str = "fblai-test") -> StuckCandidate:
    return StuckCandidate(bead_id=bead_id, kind="hung", runtime_s=runtime_s, model="sonnet")


def _crashed_candidate(bead_id: str = "fblai-crashed") -> StuckCandidate:
    return StuckCandidate(bead_id=bead_id, kind="crashed", runtime_s=0.0, model="")


def _all_clear_kwargs(kind: str = "hung", now_runtime_s: float = 2000.0) -> dict:
    """Kwargs that pass all four guards for the given kind."""
    return dict(
        bead_status="in_progress",
        future_done=False,
        assigned_worker="fblai-test",
        now_runtime_s=now_runtime_s,
        spawning_window_s=300.0,
    )


# ---------------------------------------------------------------------------
# Guard 1 — terminal-state
# ---------------------------------------------------------------------------

class TestTerminalStateGuard:
    def test_done_status_drops_candidate(self) -> None:
        """bead_status='done' → guard fires → candidate dropped."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="done",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is False

    def test_closed_status_drops_candidate(self) -> None:
        """bead_status='closed' → guard fires → candidate dropped."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="closed",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is False

    def test_in_progress_status_passes_guard1(self) -> None:
        """bead_status='in_progress' → guard 1 does not fire."""
        c = _hung_candidate()
        # Only guard 1 is relevant here; rest set to pass
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is True  # all guards pass


# ---------------------------------------------------------------------------
# Guard 2 — stale-hook
# ---------------------------------------------------------------------------

class TestStaleHookGuard:
    def test_no_assigned_worker_drops_candidate(self) -> None:
        """assigned_worker=None → guard 2 fires → candidate dropped."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker=None,      # stale hook
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is False

    def test_assigned_worker_present_passes_guard2(self) -> None:
        """assigned_worker set → guard 2 does not fire."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is True


# ---------------------------------------------------------------------------
# Guard 3 — spawning-window
# ---------------------------------------------------------------------------

class TestSpawningWindowGuard:
    def test_hung_under_window_dropped(self) -> None:
        """hung candidate with now_runtime_s < spawning_window_s → dropped."""
        c = _hung_candidate(runtime_s=100.0)
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=100.0,       # under 300s window
            spawning_window_s=300.0,
        )
        assert result is False

    def test_hung_past_window_survives_guard3(self) -> None:
        """hung candidate with now_runtime_s >= spawning_window_s → guard 3 does not fire."""
        c = _hung_candidate(runtime_s=2000.0)
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,      # 2000s > 300s window
            spawning_window_s=300.0,
        )
        assert result is True

    def test_crashed_bypasses_spawning_window(self) -> None:
        """crashed candidate is NOT dropped by the spawning-window guard even with low runtime."""
        c = _crashed_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-crashed",
            now_runtime_s=0.0,         # 0s runtime — would drop a hung candidate
            spawning_window_s=300.0,
        )
        # crashed bypasses guard 3 — should survive all guards
        assert result is True

    def test_crashed_at_zero_runtime_survives(self) -> None:
        """Confirm crashed with runtime_s=0.0 is not suppressed by spawning-window."""
        c = StuckCandidate(bead_id="fblai-crash-rt", kind="crashed", runtime_s=0.0, model="")
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-crash-rt",
            now_runtime_s=0.0,
            spawning_window_s=999999.0,  # enormous window — still bypassed for crashed
        )
        assert result is True


# ---------------------------------------------------------------------------
# Guard 4 — TOCTOU re-check
# ---------------------------------------------------------------------------

class TestTOCTOUGuard:
    def test_future_done_drops_candidate(self) -> None:
        """future_done=True at decision time → TOCTOU guard fires → dropped."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=True,           # race: future finished between detect and guard
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is False

    def test_bead_closed_at_toctou_drops_candidate(self) -> None:
        """bead_status='closed' at TOCTOU re-check → dropped (guard 1 catches this too,
        but ensuring the TOCTOU path also catches closed state is belt-and-suspenders)."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="closed",      # closed between detect and guard evaluation
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is False

    def test_fresh_status_closed_at_toctou_drops_candidate(self) -> None:
        """Guard 4 uses get_fresh_status for a true re-read at decision time.

        Simulates the race where bead_status was 'in_progress' when initially
        read (Guard 1 passes), but by the time Guard 4 fires the fresh re-read
        returns 'closed'.  Without get_fresh_status the stale 'in_progress'
        would let the candidate through; with it the race is caught.
        """
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",   # stale: Guard 1 passes with this value
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
            get_fresh_status=lambda _bead_id: "closed",  # fresh re-read at Guard 4
        )
        assert result is False, (
            "Guard 4 should have dropped the candidate after fresh re-read returned 'closed'"
        )

    def test_fresh_status_in_progress_at_toctou_survives(self) -> None:
        """Guard 4 fresh re-read returns 'in_progress' → candidate is NOT dropped."""
        c = _hung_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
            get_fresh_status=lambda _bead_id: "in_progress",
        )
        assert result is True


# ---------------------------------------------------------------------------
# Genuinely stuck candidate passes all four guards
# ---------------------------------------------------------------------------

class TestGenuinelyStuckSurvives:
    def test_hung_all_guards_pass(self) -> None:
        """A genuinely-stuck hung candidate passes all four guards."""
        c = _hung_candidate(runtime_s=2000.0)
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-test",
            now_runtime_s=2000.0,
            spawning_window_s=300.0,
        )
        assert result is True

    def test_crashed_all_guards_pass(self) -> None:
        """A genuinely-stuck crashed candidate passes all four guards."""
        c = _crashed_candidate()
        result = survives_guards(
            c,
            bead_status="in_progress",
            future_done=False,
            assigned_worker="fblai-crashed",
            now_runtime_s=0.0,
            spawning_window_s=300.0,
        )
        assert result is True
