"""reconciler.py — P2 Mayor reconciler: detect → guard-ladder → AI-judge → Mayor executes.

Stage 1 (P2.1): detect_candidates  — pure, mechanical, emits events, never acts.
Stage 2 (P2.3): survives_guards    — pure, suppresses false-positives.
Stage 3 (P2.2): reconcile          — calls AI-judge for guard-survivors; returns
                                     ReconcileAction list for the Mayor to apply.

The Mayor (run_mayor_loop main thread) is the only caller that mutates bead
state or active/recovery_blocked sets.  These functions are read-only and
deterministic given their arguments.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("reconciler")

# ---------------------------------------------------------------------------
# §1 Named constants + env overrides
# ---------------------------------------------------------------------------

LOOP_STUCK_THRESHOLD_S: float = float(
    os.environ.get("OPTIVAI_LOOP_STUCK_THRESHOLD_S", "1800")
)

LOOP_SPAWNING_WINDOW_S: float = float(
    os.environ.get("OPTIVAI_LOOP_SPAWNING_WINDOW_S", "300")
)

LOOP_MAX_RESPAWNS: int = int(
    os.environ.get("OPTIVAI_LOOP_MAX_RESPAWNS", "1")
)

# ---------------------------------------------------------------------------
# §2 Data structures
# ---------------------------------------------------------------------------

@dataclass
class StuckCandidate:
    """A bead that the detector suspects is stuck."""

    bead_id: str
    kind: str           # "crashed" | "hung"
    runtime_s: float    # now - started_at
    model: str


@dataclass
class ReconcileAction:
    """One action the Mayor should apply for a stuck candidate."""

    bead_id: str
    decision: str       # "kill" | "respawn" | "wait"


# ---------------------------------------------------------------------------
# §3 Stage 1 — Detector (pure, mechanical, never acts)
# ---------------------------------------------------------------------------

def detect_candidates(
    active: Dict[str, Any],         # Dict[str, WorkerHandle]
    recovery_blocked: Set[str],
    now: float,                     # monotonic time (passed in; no time.monotonic inside)
    stuck_threshold_s: float,
) -> List[StuckCandidate]:
    """Return candidates that appear stuck.

    Two kinds:
      crashed — bead_id is already in recovery_blocked (worker errored/timed-out
                during completion handling; the slot is occupied but the worker
                is gone; it needs the judge's decision to clear).
      hung    — bead_id is in active, its future is NOT done, and its runtime
                exceeds stuck_threshold_s.

    Pure: no I/O, no AI, no mutation of any argument.
    ``now`` is passed in so callers (tests + Mayor) control the clock.
    """
    candidates: List[StuckCandidate] = []

    # crashed: already in recovery_blocked
    for bead_id in recovery_blocked:
        # Runtime is unknown (the worker is gone); use 0.0 as the reported value.
        # The crashed kind bypasses the spawning-window guard anyway.
        candidates.append(
            StuckCandidate(
                bead_id=bead_id,
                kind="crashed",
                runtime_s=0.0,
                model="",
            )
        )

    # hung: in active, future not done, runtime > threshold
    for bead_id, handle in active.items():
        if handle.future.done():
            continue
        runtime_s = now - handle.started_at
        if runtime_s > stuck_threshold_s:
            candidates.append(
                StuckCandidate(
                    bead_id=bead_id,
                    kind="hung",
                    runtime_s=runtime_s,
                    model=handle.model,
                )
            )

    return candidates


# ---------------------------------------------------------------------------
# §4 Stage 2 — Guard-ladder (pure, suppresses false-positives)
# ---------------------------------------------------------------------------

def survives_guards(
    c: StuckCandidate,
    bead_status: str,           # current bead status (injected by Mayor)
    future_done: bool,          # is the worker future done right now?
    assigned_worker: Optional[str],  # worker currently assigned (or None)
    now_runtime_s: float,       # re-measured runtime at decision time (TOCTOU check)
    spawning_window_s: float,
    get_fresh_status: Optional[Callable[[str], str]] = None,
) -> bool:
    """Return True iff the candidate survives all guards and should reach the judge.

    Guards are applied cheapest/most-decisive first.  Any guard firing drops the
    candidate (returns False).  All four must pass (return True) to reach the judge.

    Guard 1 — terminal-state:
        Bead is already done/closed — worker finished, Mayor hasn't reaped yet.
    Guard 2 — stale-hook:
        Bead no longer assigned to this worker (closed/reassigned).
        Represented here as assigned_worker being None (the hook expired).
    Guard 3 — spawning-window:
        hung candidate whose runtime_s < spawning_window_s — still warming up.
        Crashed candidates bypass this guard (they are already past the window).
    Guard 4 — TOCTOU re-check:
        Re-read worker/bead liveness at the moment of decision.  If the future
        is now done OR the bead is now closed/done at the CURRENT moment (fresh
        re-read via get_fresh_status), race condition — drop.  Per the P2 design,
        this re-read is the decisive check; without it Guard 4 is redundant with
        Guard 1 (same stale value).  Falls back to bead_status when get_fresh_status
        is absent (backward-compat with tests that do not inject a reader).
    """
    # Guard 1: terminal-state (uses the initially-passed status)
    if bead_status in ("done", "closed"):
        return False

    # Guard 2: stale-hook
    if assigned_worker is None:
        return False

    # Guard 3: spawning-window (only for hung; crashed already past the window)
    if c.kind == "hung" and now_runtime_s < spawning_window_s:
        return False

    # Guard 4: TOCTOU re-check — fetch the CURRENT bead status at decision time.
    # For crashed candidates (kind="crashed"), future_done=True is expected — there is no
    # live future; the worker is already gone.  We only use future_done as a drop signal
    # for hung candidates (where the future finishing means the worker resolved the hang).
    if c.kind == "hung" and future_done:
        return False
    # Re-read bead status now (at decision time) rather than reusing the stale value
    # captured before the guard ladder started.  This is the TOCTOU guard's purpose.
    current_status = get_fresh_status(c.bead_id) if get_fresh_status is not None else bead_status
    if current_status in ("done", "closed"):
        return False

    return True


# ---------------------------------------------------------------------------
# §5 Stage 3 — reconcile (calls AI-judge; returns actions for the Mayor)
# ---------------------------------------------------------------------------

def reconcile(
    active: Dict[str, Any],         # Dict[str, WorkerHandle]
    recovery_blocked: Set[str],
    bead_statuses: Dict[str, str],  # bead_id → current status (injected by Mayor)
    respawn_counts: Dict[str, int], # bead_id → number of times already respawned
    cfg_stuck_threshold_s: float,
    cfg_spawning_window_s: float,
    cfg_max_respawns: int,
    now: float,
    judge: Optional[Callable[["StuckCandidate", dict], str]],
) -> List[ReconcileAction]:
    """Run the full detect → guard-ladder → AI-judge pipeline.

    Returns a list of ReconcileActions.  The Mayor applies them after this
    function returns — reconcile itself mutates nothing.

    Judge contract:
      - Takes (StuckCandidate, context_dict) → "kill" | "respawn" | "wait"
      - Any exception or None return → treated as "wait" (fail-safe)
      - Called ONLY for guard-surviving candidates
    """
    actions: List[ReconcileAction] = []

    # Stage 1: detect
    candidates = detect_candidates(active, recovery_blocked, now, cfg_stuck_threshold_s)
    if not candidates:
        return actions

    # Stage 2: guard-ladder — filter to survivors
    survivors: List[StuckCandidate] = []
    for c in candidates:
        bead_status = bead_statuses.get(c.bead_id, "unknown")

        # future_done: True if the worker handle exists and future is done
        handle = active.get(c.bead_id)
        future_done = handle.future.done() if handle is not None else True

        # assigned_worker: None if there is no live handle for this bead
        assigned_worker = c.bead_id if handle is not None or c.bead_id in recovery_blocked else None

        # now_runtime_s: re-measure from current handle; 0.0 for crashed (no handle)
        if handle is not None:
            now_runtime_s = now - handle.started_at
        else:
            now_runtime_s = 0.0

        if survives_guards(
            c,
            bead_status=bead_status,
            future_done=future_done,
            assigned_worker=assigned_worker,
            now_runtime_s=now_runtime_s,
            spawning_window_s=cfg_spawning_window_s,
            # Re-read the bead status at Guard 4 decision time from the Mayor's
            # live snapshot (bead_statuses is the Mayor's single-writer view).
            get_fresh_status=lambda bid: bead_statuses.get(bid, "unknown"),
        ):
            survivors.append(c)

    # Stage 3: AI-judge (only for guard-survivors)
    for c in survivors:
        decision = _judge_safe(judge, c, bead_statuses)

        # Respawn-cap: if already respawned cfg_max_respawns times, demote to "wait"
        if decision == "respawn":
            count = respawn_counts.get(c.bead_id, 0)
            if count >= cfg_max_respawns:
                logger.warning(
                    "reconcile: bead %s hit respawn cap (%d); demoting to 'wait'",
                    c.bead_id,
                    cfg_max_respawns,
                )
                decision = "wait"

        actions.append(ReconcileAction(bead_id=c.bead_id, decision=decision))

    return actions


def _judge_safe(
    judge: Optional[Callable[["StuckCandidate", dict], str]],
    c: StuckCandidate,
    context: dict,
) -> str:
    """Call the judge, fail-safe to 'wait' on any error or None return."""
    if judge is None:
        return "wait"
    try:
        result = judge(c, context)
        if result not in ("kill", "respawn", "wait"):
            logger.warning(
                "reconcile: judge returned unexpected value %r for bead %s; treating as 'wait'",
                result,
                c.bead_id,
            )
            return "wait"
        return result
    except Exception as exc:
        logger.warning(
            "reconcile: judge raised for bead %s: %s; failing safe to 'wait'",
            c.bead_id,
            exc,
        )
        return "wait"
