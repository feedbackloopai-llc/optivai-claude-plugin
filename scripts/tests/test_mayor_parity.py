"""test_mayor_parity.py — Cross-harness parity tests for run_mayor_loop.

Loads the shared corpus from mayor_parity_corpus.json, runs each scenario
through run_mayor_loop with scripted fakes, collects the recorded verdict,
and asserts it equals the expected_verdict in the corpus.

Verdict model (mayor_loop scenarios):
  - mayor_events_ordered: Mayor-thread events only (beads_update + beads_close),
    recorded in deterministic order. Excludes dispatch (worker threads, non-deterministic).
  - dispatch_calls_sorted: dispatch calls as a sorted list by (bead_id, tier),
    order-independent for parallel scenarios.
  - closed: bead_ids closed by the Mayor in order.
  - failed: bead_ids that completed with non-zero verify_exit.
  - stop_reason: the MayorSummary.stop_reason.

Verdict model (order_by_score scenarios):
  - Calls order_by_score(candidates, now) directly (pure function, no loop needed).
  - expected_order: bead_ids in the expected merge order (highest-score-first,
    tie-broken by bead_id lexicographic ascending).

The corpus sha256 is pinned below and verified at module import time so any
divergence between the Python and TypeScript copies fails immediately.

Run: cd /Users/erato949/dev/optivai-claude-plugin-mayor && python3 -m pytest scripts/tests/test_mayor_parity.py -q
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

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
    order_by_score,
    run_mayor_loop,
)

# ---------------------------------------------------------------------------
# Corpus loading + sha256 pin
# ---------------------------------------------------------------------------

_CORPUS_PATH = Path(__file__).parent / "mayor_parity_corpus.json"

# Byte-identity pin — same value pinned in test/mayor-parity.test.ts.
# Both tests assert their local corpus file's sha256 equals this constant.
# Value is set after the corpus is finalised and byte-identical in both repos.
CORPUS_SHA256 = "888dc81fe836fab828576f6c06f09301ae863d58191026d9cb44f2701577480f"


def _corpus_sha256() -> str:
    return hashlib.sha256(_CORPUS_PATH.read_bytes()).hexdigest()


def _load_corpus() -> dict:
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


_CORPUS = _load_corpus()


# ---------------------------------------------------------------------------
# Parity harness — verdict recorder
# ---------------------------------------------------------------------------


class VerdictRecorder:
    """Thread-safe recorder of Mayor-visible events.

    Separates Mayor-thread events (beads_update, beads_close — always deterministic)
    from worker-thread dispatch calls (non-deterministic for parallel scenarios).

    Attributes:
      mayor_events: Ordered list of Mayor-thread events (beads_update, beads_close).
      dispatch_calls: List of dispatch calls (bead_id, tier) — sorted for comparison.
      closed: bead_ids closed by Mayor, in close order.
      failed_beads: bead_ids whose verify_exit was non-zero.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mayor_events: List[dict] = []
        self.dispatch_calls: List[dict] = []
        self.closed: List[str] = []
        self.failed_beads: List[str] = []

    def record_mayor_event(self, event: dict) -> None:
        """Record a Mayor-thread event (beads_update or beads_close)."""
        with self._lock:
            self.mayor_events.append(event)

    def record_dispatch(self, bead_id: str, tier: str) -> None:
        """Record a worker dispatch call (non-deterministic ordering for parallel)."""
        with self._lock:
            self.dispatch_calls.append({"bead_id": bead_id, "tier": tier})

    def record_fail(self, bead_id: str) -> None:
        with self._lock:
            self.failed_beads.append(bead_id)


def _extract_bead_id_from_prompt(prompt: str) -> str:
    """Extract bead_id from a compose_dispatch prompt.

    Prompts start with: "You are working bead <bead_id> in <repo> ..."
    """
    for line in prompt.splitlines():
        if line.startswith("You are working bead "):
            parts = line.split()
            # "You are working bead <id> in ..."
            if len(parts) >= 5:
                return parts[4]
    return "unknown"


# ---------------------------------------------------------------------------
# Mayor-loop scenario runner
# ---------------------------------------------------------------------------


def _run_scenario(scenario: dict, tmp_path: Path) -> dict:
    """Run one mayor_loop corpus scenario through run_mayor_loop and return the observed verdict.

    Returns:
      {
        "mayor_events_ordered": [...],  # Mayor-thread events in order
        "dispatch_calls_sorted": [...],  # dispatch calls sorted by (bead_id, tier)
        "closed": [...],
        "failed": [...],
        "stop_reason": str,
      }
    """
    cfg_data = scenario["config"]
    beads_data = scenario["beads"]
    outcomes = scenario["worker_outcomes"]
    dep_chain = scenario.get("dep_chain", [])
    always_return = scenario.get("always_return_beads", False)
    judge_decisions = scenario.get("judge_decisions", {})

    # Build dep-chain: map child → {parents}
    deps: Dict[str, Set[str]] = {}
    for child, parent in dep_chain:
        deps.setdefault(child, set()).add(parent)

    # Status tracker (simulates beads DB)
    tracker_lock = threading.Lock()
    tracker: Dict[str, str] = {b["id"]: "open" for b in beads_data}

    verdict = VerdictRecorder()

    # beads_ready: returns beads that are "open" and all deps "closed".
    # When always_return_beads=True, returns all beads always.
    def _beads_ready(molecule: str) -> List[dict]:
        if always_return:
            return list(beads_data)
        with tracker_lock:
            ready = []
            for b in beads_data:
                bid = b["id"]
                if tracker.get(bid) != "open":
                    continue
                # Check all deps are closed
                if all(tracker.get(dep) == "closed" for dep in deps.get(bid, set())):
                    ready.append(b)
            return ready

    def _beads_update(bead_id: str, status: str) -> None:
        with tracker_lock:
            tracker[bead_id] = status
        # Mayor-thread event — deterministic ordering
        verdict.record_mayor_event(
            {"type": "beads_update", "bead_id": bead_id, "status": status}
        )

    def _beads_close(bead_id: str) -> None:
        with tracker_lock:
            tracker[bead_id] = "closed"
        # Mayor-thread event — deterministic ordering
        verdict.record_mayor_event({"type": "beads_close", "bead_id": bead_id})
        with verdict._lock:
            verdict.closed.append(bead_id)

    # Per-worker verify_exit lookup: keyed by bead_id.
    # We use a thread-local to carry the bead_id from dispatch into run_verify
    # without relying on event ordering (thread-safe since each worker thread
    # has its own stack frame carrying the bead_id via _mayor_worker).
    _local = threading.local()

    def _dispatch_with_bead_tracking(prompt: str, model: str, timeout_s: int) -> dict:
        """Wraps _dispatch to record the bead_id in thread-local storage."""
        bead_id = _extract_bead_id_from_prompt(prompt)
        _local.current_bead_id = bead_id
        outcome = outcomes.get(bead_id, {})
        verdict.record_dispatch(bead_id, model)
        if "error" in outcome:
            raise RuntimeError(outcome["error"])
        return {"tokens": outcome.get("tokens", 10), "output": "done"}

    def _run_verify(cmd: str, timeout_s: int) -> int:
        # Called in the same worker thread as dispatch — thread-local is set.
        bead_id = getattr(_local, "current_bead_id", None)
        if bead_id is None:
            return 1
        outcome = outcomes.get(bead_id, {})
        if "error" in outcome:
            return 1  # shouldn't reach here
        exit_code = outcome.get("verify_exit", 0)
        if exit_code != 0:
            verdict.record_fail(bead_id)
        return exit_code

    def _judge(candidate: Any, context: dict) -> str:
        bead_id = candidate.bead_id
        return judge_decisions.get(bead_id, "wait")

    cfg = RunConfig(
        molecule=cfg_data["molecule"],
        repo=cfg_data["repo"],
        branch=cfg_data["branch"],
        verify_cmd=cfg_data["verify_cmd"],
        max_iterations=cfg_data["max_iterations"],
        budget_tokens=cfg_data["budget_tokens"],
        dry_run=False,
        once=False,
        max_workers=cfg_data["max_workers"],
        stuck_threshold_s=cfg_data["stuck_threshold_s"],
        spawning_window_s=cfg_data["spawning_window_s"],
        max_respawns=cfg_data["max_respawns"],
    )

    runners = Runners(
        beads_ready=_beads_ready,
        beads_close=_beads_close,
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=_dispatch_with_bead_tracking,
        run_verify=_run_verify,
        beads_update=_beads_update,
        loop_state_path=tmp_path / "loop-state.json",
        judge=_judge if judge_decisions else None,
    )

    summary: MayorSummary = run_mayor_loop(cfg, runners)

    # Sort dispatch calls by (bead_id, tier) for order-independent comparison
    dispatch_calls_sorted = sorted(
        verdict.dispatch_calls, key=lambda e: (e["bead_id"], e["tier"])
    )

    return {
        "mayor_events_ordered": verdict.mayor_events,
        "dispatch_calls_sorted": dispatch_calls_sorted,
        "closed": verdict.closed,
        "failed": verdict.failed_beads,
        "stop_reason": summary.stop_reason,
    }


# ---------------------------------------------------------------------------
# VB2 order_by_score scenario runner
# ---------------------------------------------------------------------------


def _run_order_by_score_scenario(scenario: dict) -> List[str]:
    """Run one order_by_score corpus scenario through the pure orderer.

    Builds MergeCandidate objects from the corpus, calls order_by_score(candidates, now),
    and returns the bead_ids in highest-score-first order (tie-broken by bead_id).

    Returns:
      List of bead_ids in the order produced by order_by_score.
    """
    now: float = float(scenario["now"])
    raw_candidates: List[dict] = scenario["candidates"]

    candidates: List[MergeCandidate] = [
        MergeCandidate(
            bead_id=c["bead_id"],
            branch_name=c["branch_name"],
            worktree_path=None,
            model=c["model"],
            verified_at=float(c["verified_at"]),
            priority=int(c["priority"]),
            attempts=int(c["attempts"]),
        )
        for c in raw_candidates
    ]

    ordered = order_by_score(candidates, now)
    return [c.bead_id for c in ordered]


# ---------------------------------------------------------------------------
# Test: corpus sha256 byte-identity
# ---------------------------------------------------------------------------


class TestCorpusByteIdentity:
    """Verify the corpus file matches the pinned sha256.

    The same sha256 constant is pinned in test/mayor-parity.test.ts.
    If the two constants match, the two corpus files are byte-identical.
    """

    def test_corpus_file_exists(self) -> None:
        assert _CORPUS_PATH.exists(), f"Corpus file not found: {_CORPUS_PATH}"

    def test_corpus_sha256_matches_pin(self) -> None:
        actual = _corpus_sha256()
        assert len(actual) == 64, f"sha256 should be 64 hex chars, got: {actual!r}"
        if CORPUS_SHA256 != "PENDING":
            assert actual == CORPUS_SHA256, (
                f"Corpus sha256 mismatch — corpus file was modified without updating the pin.\n"
                f"  actual:  {actual}\n"
                f"  pinned:  {CORPUS_SHA256}\n"
                "Update CORPUS_SHA256 in both test files to: " + actual
            )
        else:
            # Pin not yet set — print for manual pinning
            print(f"\n[parity] corpus sha256 (set as CORPUS_SHA256): {actual}")

    def test_corpus_has_scenarios(self) -> None:
        scenarios = _CORPUS.get("scenarios", [])
        assert len(scenarios) >= 9, f"Expected at least 9 scenarios, got {len(scenarios)}"


# ---------------------------------------------------------------------------
# Parameterized parity tests
# ---------------------------------------------------------------------------

_SCENARIO_IDS = [s["id"] for s in _CORPUS.get("scenarios", [])]


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_mayor_parity(scenario_id: str, tmp_path: Path) -> None:
    """For each corpus scenario: dispatch to the correct runner based on scenario_type,
    then assert the observed verdict matches the expected values in the corpus."""
    scenario = next(s for s in _CORPUS["scenarios"] if s["id"] == scenario_id)
    scenario_type = scenario.get("scenario_type", "mayor_loop")

    if scenario_type == "order_by_score":
        # VB2: pure orderer — no loop involved.
        expected_order: List[str] = scenario["expected_order"]
        observed_order = _run_order_by_score_scenario(scenario)

        assert observed_order == expected_order, (
            f"[{scenario_id}] order_by_score mismatch:\n"
            f"  observed: {observed_order}\n"
            f"  expected: {expected_order}"
        )
        return

    # Default: mayor_loop scenario.
    expected = scenario["expected_verdict"]
    observed = _run_scenario(scenario, tmp_path)

    # 1. Assert stop_reason
    assert observed["stop_reason"] == expected["stop_reason"], (
        f"[{scenario_id}] stop_reason mismatch:\n"
        f"  observed: {observed['stop_reason']!r}\n"
        f"  expected: {expected['stop_reason']!r}"
    )

    # 2. Assert closed bead list (order + content)
    assert observed["closed"] == expected["closed"], (
        f"[{scenario_id}] closed mismatch:\n"
        f"  observed: {observed['closed']}\n"
        f"  expected: {expected['closed']}"
    )

    # 3. Assert Mayor-thread events in order (beads_update + beads_close)
    assert observed["mayor_events_ordered"] == expected["mayor_events_ordered"], (
        f"[{scenario_id}] mayor_events_ordered mismatch:\n"
        f"  observed:\n"
        + "\n".join(f"    {e}" for e in observed["mayor_events_ordered"])
        + "\n  expected:\n"
        + "\n".join(f"    {e}" for e in expected["mayor_events_ordered"])
    )

    # 4. Assert dispatch calls as sorted multiset (order-independent for parallel)
    assert observed["dispatch_calls_sorted"] == expected["dispatch_calls_sorted"], (
        f"[{scenario_id}] dispatch_calls_sorted mismatch:\n"
        f"  observed: {observed['dispatch_calls_sorted']}\n"
        f"  expected: {expected['dispatch_calls_sorted']}"
    )

    # 5. Assert failed bead list (ordered — verify runs sequentially per-scenario)
    assert observed["failed"] == expected["failed"], (
        f"[{scenario_id}] failed mismatch:\n"
        f"  observed: {observed['failed']}\n"
        f"  expected: {expected['failed']}"
    )
