"""test_loop_runner.py — Tests for scripts/loop_runner.py (T2).

Coverage:
  - select_next: picks highest-priority ready bead
  - compose_dispatch: gate-compliant output; contains bead id, acceptance clause,
    output-contract sentence; raises on forced non-compliant template
  - --dry-run: ZERO live invocations, ZERO beads_close calls
  - close_if_verified: green V → closed; red V → NOT closed + failure captured;
    missing V → escalate, not closed
  - termination: empty ready → "queue-empty"; max-iters reached → "max-iterations"
  - fail-open: exception in iteration → run loop continues, beads_close never called

Run: cd /Users/erato949/dev/optivai-claude-plugin/scripts && python3 -m pytest tests/test_loop_runner.py -v
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is on sys.path so loop_runner is importable
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Also ensure hooks/ is on path for dispatch_gate
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    IterationResult,
    RunConfig,
    Runners,
    RunSummary,
    close_if_verified,
    compose_dispatch,
    resolve_verify_cmd,
    route_model,
    run_iteration,
    run_loop,
    select_next,
    should_continue,
)
from dispatch_gate import evaluate_dispatch


# ---------------------------------------------------------------------------
# Fixtures
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
) -> tuple[Runners, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Return (runners, beads_close_mock, brain_recall_mock, brain_capture_mock, dispatch_mock)."""
    beads_close_mock = MagicMock()
    brain_recall_mock = MagicMock(return_value="")
    brain_capture_mock = MagicMock()
    dispatch_mock = MagicMock(return_value={"tokens": 100, "output": "agent completed"})
    verify_mock = MagicMock(return_value=verify_exit)

    runners = Runners(
        beads_ready=MagicMock(return_value=ready_beads if ready_beads is not None else []),
        beads_close=beads_close_mock,
        brain_recall=brain_recall_mock,
        brain_capture=brain_capture_mock,
        dispatch=dispatch_mock,
        run_verify=verify_mock,
    )
    return runners, beads_close_mock, brain_recall_mock, brain_capture_mock, dispatch_mock


def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="test-molecule",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=3,
        budget_tokens=10_000_000,
        dry_run=False,
        once=False,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


# ---------------------------------------------------------------------------
# select_next
# ---------------------------------------------------------------------------

class TestSelectNext:
    def test_returns_none_on_empty_list(self) -> None:
        assert select_next([]) is None

    def test_picks_highest_priority_bead(self) -> None:
        beads = [
            _make_bead("fblai-b", priority=3),
            _make_bead("fblai-a", priority=1),  # highest priority (lowest number)
            _make_bead("fblai-c", priority=2),
        ]
        result = select_next(beads)
        assert result is not None
        assert result["id"] == "fblai-a"

    def test_single_bead(self) -> None:
        bead = _make_bead("fblai-only", priority=2)
        result = select_next([bead])
        assert result is not None
        assert result["id"] == "fblai-only"

    def test_tiebreak_by_id(self) -> None:
        """When priorities are equal, tiebreaks lexicographically by id."""
        beads = [
            _make_bead("fblai-zzz", priority=2),
            _make_bead("fblai-aaa", priority=2),
        ]
        result = select_next(beads)
        assert result is not None
        assert result["id"] == "fblai-aaa"


# ---------------------------------------------------------------------------
# compose_dispatch
# ---------------------------------------------------------------------------

class TestComposeDispatch:
    def test_output_is_gate_compliant(self) -> None:
        """The composed prompt must pass evaluate_dispatch(...).compliant."""
        bead = _make_bead("fblai-xyz", title="Add token counter", body="")
        prompt = compose_dispatch(bead, "/repo", "main", "python3 -m pytest -q")
        verdict = evaluate_dispatch(prompt, mode="warn")
        assert verdict["compliant"] is True, (
            f"compose_dispatch produced non-compliant prompt.\n"
            f"missing={verdict['missing']}\nwarnings={verdict['warnings']}\n"
            f"prompt:\n{prompt}"
        )

    def test_contains_bead_id(self) -> None:
        bead = _make_bead("fblai-abc123", title="Some task")
        prompt = compose_dispatch(bead, "/repo", "main", "true")
        assert "fblai-abc123" in prompt

    def test_contains_acceptance_or_termination_clause(self) -> None:
        """Prompt must contain an explicit acceptance/termination clause."""
        bead = _make_bead("fblai-t1", title="Run tests")
        prompt = compose_dispatch(bead, "/repo", "main", "pytest")
        # Acceptance / termination criterion section must be present
        assert "termination criterion" in prompt.lower() or "acceptance" in prompt.lower()

    def test_contains_output_contract_sentence(self) -> None:
        """Prompt must contain an output contract instruction."""
        bead = _make_bead("fblai-t2", title="Implement feature")
        prompt = compose_dispatch(bead, "/repo", "main", "pytest")
        # Output contract: "Report a one-paragraph summary..."
        lower = prompt.lower()
        assert "report" in lower or "summary" in lower or "return" in lower

    def test_extracts_acceptance_from_bead_body(self) -> None:
        """When bead body has Acceptance: block, it appears in the prompt."""
        bead = _make_bead(
            "fblai-t3",
            title="Fix bug",
            body="Do the work.\n\nAcceptance: all 5 tests pass\n  pytest exits 0",
        )
        prompt = compose_dispatch(bead, "/repo", "main", "pytest -q")
        assert "all 5 tests pass" in prompt

    def test_raises_on_forced_non_compliant(self) -> None:
        """Patching evaluate_dispatch to return non-compliant causes compose_dispatch to raise."""
        bead = _make_bead("fblai-bad", title="Bad bead")
        with patch(
            "loop_runner.evaluate_dispatch",
            return_value={
                "checked": True,
                "compliant": False,
                "missing": ["termination criterion missing"],
                "warnings": [],
                "block": False,
            },
        ):
            with pytest.raises(ValueError, match="non-compliant"):
                compose_dispatch(bead, "/repo", "main", "true")

    def test_does_not_paste_content_inline(self) -> None:
        """Prompt must reference paths, not paste file content inline."""
        body = "Read scripts/loop_runner.py and implement the feature."
        bead = _make_bead("fblai-t4", title="Impl task", body=body)
        prompt = compose_dispatch(bead, "/repo", "main", "pytest")
        # Should reference the path, not paste a code block
        assert "loop_runner.py" in prompt
        # Should NOT contain a large fenced block (over 500 chars within ```)
        import re
        blocks = re.findall(r"```.*?```", prompt, re.DOTALL)
        for block in blocks:
            assert len(block) < 500, f"Suspicious large inline block: {block[:100]}..."


# ---------------------------------------------------------------------------
# --dry-run semantics
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_zero_dispatch_calls(self) -> None:
        """--dry-run must not call dispatch at all."""
        bead = _make_bead("fblai-dr1", priority=1)
        runners, close_mock, _, _, dispatch_mock = _make_runners(
            ready_beads=[bead], verify_exit=0
        )
        cfg = _make_cfg(dry_run=True)
        run_loop(cfg, runners)
        dispatch_mock.assert_not_called()

    def test_dry_run_zero_beads_close_calls(self) -> None:
        """--dry-run must not call beads_close."""
        bead = _make_bead("fblai-dr2", priority=1)
        runners, close_mock, _, _, _ = _make_runners(
            ready_beads=[bead], verify_exit=0
        )
        cfg = _make_cfg(dry_run=True, max_iterations=1)
        run_loop(cfg, runners)
        close_mock.assert_not_called()

    def test_dry_run_zero_verify_calls(self) -> None:
        """--dry-run must not call run_verify."""
        bead = _make_bead("fblai-dr3", priority=1)
        runners, _, _, _, _ = _make_runners(ready_beads=[bead], verify_exit=0)
        cfg = _make_cfg(dry_run=True, max_iterations=1)
        run_loop(cfg, runners)
        runners.run_verify.assert_not_called()


# ---------------------------------------------------------------------------
# close_if_verified
# ---------------------------------------------------------------------------

class TestCloseIfVerified:
    def test_green_verify_closes_bead(self) -> None:
        bead = _make_bead("fblai-cv1")
        runners, close_mock, _, capture_mock, _ = _make_runners(verify_exit=0)
        result = close_if_verified(bead, "pytest -q", runners)
        assert result.outcome == "closed"
        assert result.verify_exit == 0
        close_mock.assert_called_once_with("fblai-cv1")
        # brain_capture called with decision type
        capture_mock.assert_called_once()
        call_args = capture_mock.call_args[0]
        assert "decision" in call_args[1]

    def test_red_verify_does_not_close_bead(self) -> None:
        bead = _make_bead("fblai-cv2")
        runners, close_mock, _, capture_mock, _ = _make_runners(verify_exit=1)
        result = close_if_verified(bead, "pytest -q", runners)
        assert result.outcome == "failed"
        assert result.verify_exit == 1
        close_mock.assert_not_called()
        # failure captured to brain
        capture_mock.assert_called_once()
        call_args = capture_mock.call_args[0]
        assert "pattern" in call_args[1]

    def test_missing_verify_escalates_not_closed(self) -> None:
        bead = _make_bead("fblai-cv3")
        runners, close_mock, _, capture_mock, _ = _make_runners()
        result = close_if_verified(bead, None, runners)
        assert result.outcome == "escalated"
        assert result.verify_exit is None
        close_mock.assert_not_called()
        capture_mock.assert_called_once()

    def test_empty_string_verify_escalates(self) -> None:
        """Empty string verify cmd is treated as missing → escalate."""
        bead = _make_bead("fblai-cv4")
        runners, close_mock, _, _, _ = _make_runners()
        result = close_if_verified(bead, "", runners)
        assert result.outcome == "escalated"
        close_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Termination conditions
# ---------------------------------------------------------------------------

class TestTermination:
    def test_empty_queue_stops_with_queue_empty(self) -> None:
        """When beads_ready returns [], run_loop stops with 'queue-empty'."""
        runners, _, _, _, _ = _make_runners(ready_beads=[])
        cfg = _make_cfg()
        summary = run_loop(cfg, runners)
        assert summary.stop_reason == "queue-empty"
        # No beads closed because queue was empty from the start
        assert summary.beads_closed == 0

    def test_max_iterations_reached_stops_with_max_iterations(self) -> None:
        """When max_iterations is hit, stop_reason is 'max-iterations'."""
        bead = _make_bead("fblai-mi1", priority=1)
        # Bead is always ready but V always fails → never closed → never queue-empty
        runners, close_mock, _, _, _ = _make_runners(
            ready_beads=[bead], verify_exit=1
        )
        cfg = _make_cfg(max_iterations=2, once=False)
        summary = run_loop(cfg, runners)
        assert summary.stop_reason == "max-iterations"
        assert summary.iterations == 2
        close_mock.assert_not_called()

    def test_once_flag_runs_exactly_one_iteration(self) -> None:
        """--once exits after exactly one full iteration."""
        bead = _make_bead("fblai-once", priority=1)
        runners, close_mock, _, _, dispatch_mock = _make_runners(
            ready_beads=[bead], verify_exit=0
        )
        cfg = _make_cfg(once=True, max_iterations=25)
        summary = run_loop(cfg, runners)
        assert summary.stop_reason == "once"
        assert summary.iterations == 1
        dispatch_mock.assert_called_once()
        close_mock.assert_called_once_with("fblai-once")


# ---------------------------------------------------------------------------
# Fail-open: exception in iteration does not crash run loop
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_exception_in_dispatch_does_not_crash_loop(self) -> None:
        """An exception inside dispatch must not crash run_loop.
        The iteration is recorded as 'failed', beads_close is never called."""
        bead = _make_bead("fblai-fo1", priority=1)
        runners, close_mock, _, _, dispatch_mock = _make_runners(ready_beads=[bead])
        dispatch_mock.side_effect = RuntimeError("agent exploded")
        cfg = _make_cfg(max_iterations=1)
        # Should not raise
        summary = run_loop(cfg, runners)
        assert summary.stop_reason == "max-iterations"
        assert summary.beads_closed == 0
        close_mock.assert_not_called()

    def test_exception_in_run_iteration_does_not_crash_loop(self) -> None:
        """Even if run_iteration raises at top level, run_loop must not crash."""
        runners, close_mock, _, _, _ = _make_runners(ready_beads=[_make_bead("fblai-fo2")])
        cfg = _make_cfg(max_iterations=1)

        with patch("loop_runner.run_iteration", side_effect=RuntimeError("boom")):
            # Should not raise
            summary = run_loop(cfg, runners)

        assert summary.beads_closed == 0
        close_mock.assert_not_called()

    def test_beads_close_never_called_on_exception(self) -> None:
        """Verify beads_close is never called when an exception occurs."""
        bead = _make_bead("fblai-fo3", priority=1)
        runners, close_mock, _, _, dispatch_mock = _make_runners(ready_beads=[bead])
        dispatch_mock.side_effect = Exception("unexpected")
        cfg = _make_cfg(max_iterations=2)
        run_loop(cfg, runners)
        close_mock.assert_not_called()


# ---------------------------------------------------------------------------
# route_model
# ---------------------------------------------------------------------------

class TestRouteModel:
    def test_tier_label_overrides(self) -> None:
        bead = _make_bead(labels=["tier:design"])
        assert route_model(bead) == "opus"

    def test_tier_implement_routes_to_sonnet(self) -> None:
        bead = _make_bead(labels=["tier:implement"])
        assert route_model(bead) == "sonnet"

    def test_tier_busywork_routes_to_haiku(self) -> None:
        bead = _make_bead(labels=["tier:busywork"])
        assert route_model(bead) == "haiku"

    def test_design_keyword_in_title(self) -> None:
        bead = _make_bead(title="D1 DESIGN loop architecture")
        assert route_model(bead) == "opus"

    def test_default_routes_to_sonnet(self) -> None:
        bead = _make_bead(title="Implement the feature")
        assert route_model(bead) == "sonnet"


class TestRouteModelFableGate:
    """FABLE-CORE (fblai-3hf0c): the fail-closed Fable gate + the tier:<model-name> normalization fix.

    CONSUMER tests - drive the REAL route_model, not a reimplementation. The never-Fable-on-security invariant
    (criterion 2) is the whole point: NO label combo routes a security-marked bead to fable.
    """

    # --- the fail-closed Fable gate (criterion 1) ---
    def test_fable_with_ready_routes_to_fable(self) -> None:
        assert route_model(_make_bead(labels=["tier:fable", "fable-ready"])) == "fable"

    def test_fable_without_ready_downgrades_to_opus(self) -> None:
        # fail-closed: tier:fable but NO fable-ready -> opus (we downgrade; never let Fable refuse-and-fallback)
        assert route_model(_make_bead(labels=["tier:fable"])) == "opus"

    # --- NEVER Fable on security (criterion 2 - defense-in-depth, the whole point) ---
    def test_fable_ready_plus_security_label_forced_to_opus(self) -> None:
        assert route_model(_make_bead(labels=["tier:fable", "fable-ready", "security"])) == "opus"

    def test_fable_ready_plus_security_epic_forced_to_opus(self) -> None:
        for epic in ("epic:harness-hardening", "epic:multi-tenant-isolation", "epic:per-user-isolation"):
            bead = _make_bead(labels=["tier:fable", "fable-ready", epic])
            assert route_model(bead) == "opus", epic

    def test_fable_ready_plus_auth_token_forced_to_opus(self) -> None:
        # substring token match: authz / oauth / crypto-x all block (over-block by design = safe)
        for lbl in ("auth", "authz", "oauth", "cyber", "crypto", "access-control", "exploit"):
            bead = _make_bead(labels=["tier:fable", "fable-ready", lbl])
            assert route_model(bead) == "opus", lbl

    def test_no_combo_reaches_fable_when_security_marked(self) -> None:
        # tier:fable + fable-ready + BOTH a security label AND a security epic -> still opus (security always wins)
        bead = _make_bead(labels=["tier:fable", "fable-ready", "security", "epic:harness-hardening"])
        assert route_model(bead) == "opus"

    # --- the tier:<model-name> normalization fix (criterion 3) ---
    def test_tier_opus_routes_to_opus(self) -> None:
        # previously fell through to title inference (LOOP_MODEL_MAP keys are effort classes, not model names)
        assert route_model(_make_bead(labels=["tier:opus"])) == "opus"

    def test_tier_sonnet_routes_to_sonnet(self) -> None:
        assert route_model(_make_bead(labels=["tier:sonnet"])) == "sonnet"

    def test_tier_haiku_routes_to_haiku(self) -> None:
        assert route_model(_make_bead(labels=["tier:haiku"])) == "haiku"

    def test_tier_model_name_authoritative_over_title(self) -> None:
        # tier:opus wins over a busywork-keyword title - the normalization is authoritative, not inferred
        assert route_model(_make_bead(labels=["tier:opus"], title="trivial rename cleanup")) == "opus"

    # --- Gate-2 B1/I1: the BROAD security vocabulary a narrow token set slipped to Fable (the false negatives) ---
    def test_security_nouns_never_reach_fable(self) -> None:
        for lbl in (
            "credential", "secret", "rbac", "permission", "encryption", "decrypt", "cipher",
            "sandbox", "vuln", "vulnerability", "cve", "pentest", "xss", "csrf", "ssrf", "injection",
            "escalation", "privilege", "sso", "saml", "oidc", "jwt", "mfa", "password", "session",
            "token", "tls", "ssl", "pki", "hsm", "key", "isolation", "tenant", "sovereign", "egress",
            "deas", "pii", "gdpr", "firewall", "malware",
            # epic variants (I1): the isolation/hardening/tenant tokens catch these, not the exact epic set
            "epic:tenant-isolation", "epic:harness-hardening-v2", "epic:per-tenant-isolation",
        ):
            assert route_model(_make_bead(labels=["tier:fable", "fable-ready", lbl])) == "opus", lbl

    def test_security_in_free_text_forces_opus(self) -> None:
        # The second net (B1): a bead with NO security LABEL but a security-titled/bodied task never reaches fable.
        assert route_model(
            _make_bead(labels=["tier:fable", "fable-ready"], title="Rewrite the credential store cache")
        ) == "opus"
        assert route_model({
            "id": "x", "title": "Design the caching layer",
            "description": "touches the oauth token refresh path", "labels": ["tier:fable", "fable-ready"],
        }) == "opus"

    def test_benign_bright_bead_still_reaches_fable(self) -> None:
        # The over-block must NOT swallow a genuinely non-security bright bead - else fable is never usable.
        assert route_model(
            _make_bead(labels=["tier:fable", "fable-ready"], title="Design a novel document-layout algorithm")
        ) == "fable"


# ---------------------------------------------------------------------------
# resolve_verify_cmd
# ---------------------------------------------------------------------------

class TestResolveVerifyCmd:
    def test_cli_flag_wins(self) -> None:
        bead = _make_bead(labels=["verify:pytest special"])
        result = resolve_verify_cmd(bead, "custom_cmd")
        assert result == "custom_cmd"

    def test_bead_label_verify_used_when_no_cli(self) -> None:
        bead = _make_bead(labels=["verify:npm test"])
        result = resolve_verify_cmd(bead, "")
        assert result == "npm test"

    def test_repo_default_used_when_no_label(self) -> None:
        bead = _make_bead(labels=[])
        result = resolve_verify_cmd(bead, "")
        assert result is not None
        assert len(result) > 0


# ---------------------------------------------------------------------------
# should_continue (budget governor)
# ---------------------------------------------------------------------------

class TestShouldContinue:
    def _base_cfg(self, **kw) -> RunConfig:
        defaults = dict(
            molecule="m",
            repo="/r",
            branch="main",
            verify_cmd="true",
            max_iterations=10,
            budget_tokens=1_000_000,
        )
        defaults.update(kw)
        return RunConfig(**defaults)

    def _summary(self, **kw) -> RunSummary:
        s = RunSummary(stop_reason="")
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def test_stops_at_max_iterations(self) -> None:
        cfg = self._base_cfg(max_iterations=5)
        s = self._summary(iterations=5, total_tokens=0, consecutive_zero_close=0)
        ok, reason = should_continue(s, cfg)
        assert not ok
        assert reason == "max-iterations"

    def test_stops_at_budget_exhausted(self) -> None:
        cfg = self._base_cfg(budget_tokens=1000)
        s = self._summary(iterations=1, total_tokens=1001, consecutive_zero_close=0)
        ok, reason = should_continue(s, cfg)
        assert not ok
        assert reason == "budget-exhausted"

    def test_continues_within_budget(self) -> None:
        cfg = self._base_cfg(max_iterations=10, budget_tokens=1_000_000)
        s = self._summary(iterations=5, total_tokens=100, consecutive_zero_close=0)
        ok, reason = should_continue(s, cfg)
        assert ok
        assert reason == ""
