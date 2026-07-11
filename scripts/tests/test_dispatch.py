"""test_dispatch.py — P1.1 dispatch-path test coverage.

Tests for _mayor_worker dispatch logic (bead fblai-xqbuj):
1. route_model tier mapping (design→opus, implement→sonnet, busywork→haiku, default→sonnet)
2. Worker gate-rejection: compose_dispatch raises ValueError → WorkerResult.error set,
   no bead-state mutation, brain_capture called.
3. Worker dispatch-exception: runners.dispatch raises → WorkerResult.verify_exit=None,
   WorkerResult.error set, no crash.
4. Single-writer invariant: _mayor_worker NEVER calls beads_close or beads_update.

Run: python3 -m pytest scripts/tests/test_dispatch.py -q
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ and hooks/ are on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    LOOP_MODEL_MAP,
    RunConfig,
    Runners,
    WorkerResult,
    _mayor_worker,
    compose_dispatch,
    route_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bead(
    bead_id: str = "fblai-test",
    title: str = "Test bead",
    priority: int = 2,
    labels: Optional[List[str]] = None,
    body: str = "",
    description: str = "",
) -> dict:
    return {
        "id": bead_id,
        "title": title,
        "priority": priority,
        "labels": labels or [],
        # Real beads JSON carries the task detail in `description`; keep `body` too so
        # older fixtures still compose (the runner reads description, then body).
        "description": description,
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


def _make_runners(
    *,
    dispatch_fn=None,
    verify_exit: int = 0,
    brain_capture_mock=None,
) -> Runners:
    """Build a Runners instance that fails immediately if beads_close or beads_update
    is called from a worker thread (enforcing the single-writer invariant in tests)."""
    _brain_capture = brain_capture_mock if brain_capture_mock is not None else MagicMock()

    def _fail_on_worker_write(label: str):
        """Return a callable that fails with AssertionError if called from a worker thread."""
        def _fn(*args, **kwargs):
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"{label} called from worker thread '{t.name}' — "
                "violates single-writer invariant"
            )
        return _fn

    def _dispatch(prompt: str, model: str, timeout_s: int) -> dict:
        if dispatch_fn is not None:
            return dispatch_fn(prompt, model, timeout_s)
        return {"tokens": 10, "output": "done"}

    return Runners(
        beads_ready=MagicMock(return_value=[]),
        beads_close=_fail_on_worker_write("beads_close"),
        beads_update=_fail_on_worker_write("beads_update"),
        brain_recall=MagicMock(return_value=""),
        brain_capture=_brain_capture,
        dispatch=_dispatch,
        run_verify=MagicMock(return_value=verify_exit),
    )


# ---------------------------------------------------------------------------
# Test 1 — route_model tier mapping
# ---------------------------------------------------------------------------

class TestRouteModelTierMapping:
    """route_model maps bead tier labels to the correct model strings."""

    def test_design_label_maps_to_opus(self) -> None:
        bead = _make_bead(labels=["tier:design"])
        assert route_model(bead) == LOOP_MODEL_MAP["design"]
        assert route_model(bead) == "opus"

    def test_implement_label_maps_to_sonnet(self) -> None:
        bead = _make_bead(labels=["tier:implement"])
        assert route_model(bead) == LOOP_MODEL_MAP["implement"]
        assert route_model(bead) == "sonnet"

    def test_busywork_label_maps_to_haiku(self) -> None:
        bead = _make_bead(labels=["tier:busywork"])
        assert route_model(bead) == LOOP_MODEL_MAP["busywork"]
        assert route_model(bead) == "haiku"

    def test_no_label_defaults_to_sonnet(self) -> None:
        """A bead with no tier label and no title keywords defaults to sonnet."""
        bead = _make_bead(labels=[], title="Fix the thing")
        assert route_model(bead) == LOOP_MODEL_MAP["implement"]
        assert route_model(bead) == "sonnet"

    def test_title_keyword_design_maps_to_opus(self) -> None:
        """Title keyword 'design' infers opus."""
        bead = _make_bead(title="design the schema")
        assert route_model(bead) == "opus"

    def test_title_keyword_architect_maps_to_opus(self) -> None:
        bead = _make_bead(title="architect the service layer")
        assert route_model(bead) == "opus"

    def test_title_keyword_plan_maps_to_opus(self) -> None:
        bead = _make_bead(title="plan the migration")
        assert route_model(bead) == "opus"

    def test_title_keyword_busywork_maps_to_haiku(self) -> None:
        bead = _make_bead(title="busywork: rename all files")
        assert route_model(bead) == "haiku"

    def test_title_keyword_cleanup_maps_to_haiku(self) -> None:
        bead = _make_bead(title="cleanup old log files")
        assert route_model(bead) == "haiku"

    def test_direct_effort_label_design(self) -> None:
        """Direct effort label (not tier:) also works for 'design'."""
        bead = _make_bead(labels=["design"])
        assert route_model(bead) == "opus"

    def test_direct_effort_label_busywork(self) -> None:
        bead = _make_bead(labels=["busywork"])
        assert route_model(bead) == "haiku"

    def test_model_map_has_all_three_tiers(self) -> None:
        """Verify the LOOP_MODEL_MAP constant itself has the three expected keys."""
        assert "design" in LOOP_MODEL_MAP
        assert "implement" in LOOP_MODEL_MAP
        assert "busywork" in LOOP_MODEL_MAP
        assert LOOP_MODEL_MAP["design"] == "opus"
        assert LOOP_MODEL_MAP["implement"] == "sonnet"
        assert LOOP_MODEL_MAP["busywork"] == "haiku"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Regression — compose_dispatch must carry the bead DESCRIPTION, not just the
# title. beads JSON emits the task detail under `description`; the runner used to
# read only `body` (which beads never populates), silently dropping the entire
# dispatch contract so workers got the bare title. (bug fblai-jeba9)
# ---------------------------------------------------------------------------
class TestComposeDispatchIncludesDescription:
    def test_description_flows_into_prompt(self) -> None:
        marker = "subtract a MARGINAL veracity penalty in scripts/open_brain.py at line 3855"
        bead = _make_bead(
            title="VL6-core: veracity penalty",
            description=(
                f"{marker}. Acceptance: python3 -m pytest scripts/tests/x.py -q "
                "passes with exit 0."
            ),
        )
        prompt = compose_dispatch(
            bead, "/repo", "main", "python3 -m pytest scripts/tests/x.py -q"
        )
        assert marker in prompt, "the bead description must reach the worker prompt"

    def test_falls_back_to_body_when_no_description(self) -> None:
        marker = "legacy body detail in scripts/foo.py"
        bead = _make_bead(
            title="t", description="", body=f"{marker}. Acceptance: run true exits 0."
        )
        prompt = compose_dispatch(bead, "/repo", "main", "true")
        assert marker in prompt


# Test 2 — Worker gate-rejection (compose_dispatch raises ValueError)
# ---------------------------------------------------------------------------

class TestWorkerGateRejection:
    """When compose_dispatch raises ValueError, _mayor_worker returns a WorkerResult
    with error set, does NOT raise, does NOT mutate bead state, and calls brain_capture."""

    def test_gate_rejection_sets_error_on_worker_result(self) -> None:
        """A non-compliant bead prompt causes _mayor_worker to return error, not raise."""
        bead = _make_bead("fblai-gate", title="")  # empty title → likely non-compliant
        cfg = _make_cfg()
        brain_capture_mock = MagicMock()
        runners = _make_runners(brain_capture_mock=brain_capture_mock)

        # Force compose_dispatch to raise ValueError regardless of actual compliance
        with patch("loop_runner.compose_dispatch", side_effect=ValueError("gate blocked")):
            result = _mayor_worker(bead, cfg, runners)

        assert result.error is not None, "WorkerResult.error must be set on gate-rejection"
        assert isinstance(result.error, ValueError)
        assert result.bead_id == "fblai-gate"

    def test_gate_rejection_does_not_raise(self) -> None:
        """_mayor_worker must never raise — it catches ValueError and returns WorkerResult."""
        bead = _make_bead("fblai-noraise")
        cfg = _make_cfg()
        runners = _make_runners()

        with patch("loop_runner.compose_dispatch", side_effect=ValueError("gate blocked")):
            # Must not raise
            result = _mayor_worker(bead, cfg, runners)

        assert result is not None

    def test_gate_rejection_calls_brain_capture(self) -> None:
        """When gate-blocked, the worker must call runners.brain_capture to record the event."""
        bead = _make_bead("fblai-cap-gate")
        cfg = _make_cfg()
        brain_capture_mock = MagicMock()
        runners = _make_runners(brain_capture_mock=brain_capture_mock)

        with patch("loop_runner.compose_dispatch", side_effect=ValueError("not compliant")):
            _mayor_worker(bead, cfg, runners)

        brain_capture_mock.assert_called(), (
            "brain_capture must be called when gate-blocked to record the event"
        )

    def test_gate_rejection_does_not_mutate_bead_state(self) -> None:
        """beads_close and beads_update must NOT be called when gate-blocked.
        The _fail_on_worker_write fakes in _make_runners enforce this — they would
        raise AssertionError if called from a worker thread."""
        bead = _make_bead("fblai-nomutate")
        cfg = _make_cfg()
        beads_close_called = [False]
        beads_update_called = [False]

        def _spy_close(bead_id: str) -> None:
            beads_close_called[0] = True

        def _spy_update(bead_id: str, status: str) -> None:
            beads_update_called[0] = True

        runners = _make_runners()
        runners.beads_close = _spy_close
        runners.beads_update = _spy_update

        with patch("loop_runner.compose_dispatch", side_effect=ValueError("blocked")):
            _mayor_worker(bead, cfg, runners)

        assert not beads_close_called[0], "beads_close must not be called on gate-rejection"
        assert not beads_update_called[0], "beads_update must not be called on gate-rejection"


# ---------------------------------------------------------------------------
# Test 3 — Worker dispatch-exception
# ---------------------------------------------------------------------------

class TestWorkerDispatchException:
    """When runners.dispatch raises, _mayor_worker returns verify_exit=None + error set, no crash."""

    def test_dispatch_exception_sets_error(self) -> None:
        """A raising dispatch → error is captured in WorkerResult."""
        bead = _make_bead("fblai-dexc")
        cfg = _make_cfg()

        def _raising_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            raise RuntimeError("network failure")

        runners = _make_runners(dispatch_fn=_raising_dispatch)
        result = _mayor_worker(bead, cfg, runners)

        assert result.error is not None
        assert isinstance(result.error, RuntimeError)
        assert "network failure" in str(result.error)

    def test_dispatch_exception_sets_verify_exit_none(self) -> None:
        """A raising dispatch → verify_exit must be None (V was never run)."""
        bead = _make_bead("fblai-dve")
        cfg = _make_cfg()

        def _raising_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            raise ConnectionError("timeout")

        runners = _make_runners(dispatch_fn=_raising_dispatch)
        result = _mayor_worker(bead, cfg, runners)

        assert result.verify_exit is None, (
            f"verify_exit must be None when dispatch raised, got: {result.verify_exit}"
        )

    def test_dispatch_exception_does_not_raise(self) -> None:
        """_mayor_worker must not propagate the dispatch exception — it returns WorkerResult."""
        bead = _make_bead("fblai-dnoraise")
        cfg = _make_cfg()

        def _always_raising(prompt: str, model: str, timeout_s: int) -> dict:
            raise RuntimeError("boom")

        runners = _make_runners(dispatch_fn=_always_raising)

        # Must not raise
        result = _mayor_worker(bead, cfg, runners)
        assert result is not None

    def test_dispatch_exception_preserves_bead_id(self) -> None:
        """WorkerResult.bead_id must match the dispatched bead even on exception."""
        bead = _make_bead("fblai-idcheck")
        cfg = _make_cfg()

        def _raising_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            raise ValueError("bad input")

        runners = _make_runners(dispatch_fn=_raising_dispatch)
        result = _mayor_worker(bead, cfg, runners)

        assert result.bead_id == "fblai-idcheck"


# ---------------------------------------------------------------------------
# Test 4 — Single-writer invariant: worker never calls beads_close/beads_update
# ---------------------------------------------------------------------------

class TestSingleWriterInvariantDispatch:
    """_mayor_worker must NEVER call beads_close or beads_update directly.
    We verify this by asserting those callables are never invoked inside the worker."""

    def test_successful_worker_does_not_call_beads_close(self) -> None:
        """Even when the worker succeeds (verify_exit=0), it must NOT close the bead —
        that is exclusively the Mayor's responsibility."""
        bead = _make_bead("fblai-sw-close")
        cfg = _make_cfg()
        beads_close_calls = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        runners = _make_runners(verify_exit=0)
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0, (
            f"beads_close was called {len(beads_close_calls)} time(s) from within "
            "_mayor_worker — violates single-writer invariant. "
            "Only the Mayor (main thread) may close beads."
        )

    def test_successful_worker_does_not_call_beads_update(self) -> None:
        """_mayor_worker must not call beads_update (only Mayor may mark in_progress)."""
        bead = _make_bead("fblai-sw-update")
        cfg = _make_cfg()
        beads_update_calls = []

        def _spy_update(bead_id: str, status: str) -> None:
            beads_update_calls.append((bead_id, status))

        runners = _make_runners(verify_exit=0)
        runners.beads_update = _spy_update

        _mayor_worker(bead, cfg, runners)

        assert len(beads_update_calls) == 0, (
            f"beads_update was called {len(beads_update_calls)} time(s) from within "
            "_mayor_worker — violates single-writer invariant."
        )

    def test_failing_worker_does_not_call_beads_close(self) -> None:
        """When verify_exit=1, the worker must not attempt to close the bead."""
        bead = _make_bead("fblai-sw-fail")
        cfg = _make_cfg()
        beads_close_calls = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        runners = _make_runners(verify_exit=1)
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0, (
            "beads_close was called despite verify_exit=1 — worker must not close beads"
        )

    def test_gate_rejected_worker_does_not_call_beads_close(self) -> None:
        """A gate-rejected worker must not call beads_close."""
        bead = _make_bead("fblai-sw-gate")
        cfg = _make_cfg()
        beads_close_calls = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        runners = _make_runners()
        runners.beads_close = _spy_close

        with patch("loop_runner.compose_dispatch", side_effect=ValueError("blocked")):
            _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0

    def test_exception_worker_does_not_call_beads_close(self) -> None:
        """An exception-raising dispatch must not trigger beads_close."""
        bead = _make_bead("fblai-sw-exc")
        cfg = _make_cfg()
        beads_close_calls = []

        def _spy_close(bead_id: str) -> None:
            beads_close_calls.append(bead_id)

        def _raising_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            raise RuntimeError("crash")

        runners = _make_runners(dispatch_fn=_raising_dispatch)
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(beads_close_calls) == 0
