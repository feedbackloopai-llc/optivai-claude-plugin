"""loop_runner.py — Claude-side OptivAI Loop runner engine (T2).

Implements the Python / claude-plugin binding column of the D1 design
(docs/plans/2026-06-19-loop-runner-design.md §1–§7).

Architecture
------------
All side-effecting calls (beads, claude -p, V, open_brain.py) are injected
via a Runners dataclass so tests can pass fakes without monkeypatching.

The engine functions are pure given the injected runners:
  select_next(ready)          → bead | None
  compose_dispatch(...)       → str  (self-checked, raises if non-compliant)
  route_model(bead)           → "opus" | "sonnet" | "haiku"
  close_if_verified(...)      → IterationResult-shaped outcome dict
  run_iteration(cfg, runners) → IterationResult

CLI: --molecule --verify-cmd [--max-iterations N] [--budget-tokens N]
     [--dry-run] [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Resolve the scripts/ directory so we can import dispatch_gate from hooks/
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"

if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from dispatch_gate import evaluate_dispatch  # noqa: E402

logger = logging.getLogger("loop_runner")

# ---------------------------------------------------------------------------
# §1 Named constants + env overrides
# ---------------------------------------------------------------------------

LOOP_MAX_ITERATIONS: int = int(os.environ.get("OPTIVAI_LOOP_MAX_ITERATIONS", "25"))
LOOP_BUDGET_TOKENS: int = int(os.environ.get("OPTIVAI_LOOP_BUDGET_TOKENS", "2000000"))
LOOP_NOPROGRESS_K: int = int(os.environ.get("OPTIVAI_LOOP_NOPROGRESS_K", "2"))
LOOP_MODEL_MAP: dict = {"design": "opus", "implement": "sonnet", "busywork": "haiku"}
LOOP_VERIFY_TIMEOUT_S: int = int(os.environ.get("OPTIVAI_LOOP_VERIFY_TIMEOUT_S", "900"))
LOOP_ITER_TIMEOUT_S: int = int(os.environ.get("OPTIVAI_LOOP_ITER_TIMEOUT_S", "1800"))

# Default verify command for this repo when none is specified
_REPO_DEFAULT_VERIFY_CMD = "cd /Users/erato949/dev/optivai-claude-plugin/scripts && python3 -m pytest -q"

# ---------------------------------------------------------------------------
# §2 Iteration state object
# ---------------------------------------------------------------------------

@dataclass
class IterationResult:
    """Result of one loop iteration (§2 of the D1 design)."""

    bead_id: Optional[str]          # bead worked this iteration (None if none ready)
    outcome: str                    # "closed"|"failed"|"escalated"|"empty"|"gate-blocked"
    tier: Optional[str]             # "opus"|"sonnet"|"haiku"|None
    verify_exit: Optional[int]      # exit code of V (None if not run)
    tokens_spent: int               # output tokens this iteration
    note: str                       # human-readable summary


@dataclass
class RunSummary:
    """Totals accumulated across all iterations of one run."""

    stop_reason: str                        # why the run stopped
    iterations: int = 0
    total_tokens: int = 0
    beads_closed: int = 0
    consecutive_zero_close: int = 0
    results: List[IterationResult] = field(default_factory=list)


@dataclass
class RunConfig:
    """Immutable runtime configuration for a loop run."""

    molecule: str
    repo: str
    branch: str
    verify_cmd: str
    max_iterations: int = LOOP_MAX_ITERATIONS
    budget_tokens: int = LOOP_BUDGET_TOKENS
    dry_run: bool = False
    once: bool = False


# ---------------------------------------------------------------------------
# Runners dataclass — injected side effects (mockable in tests)
# ---------------------------------------------------------------------------

@dataclass
class Runners:
    """Side-effecting callables, injectable for testing.

    Each callable signature:
      beads_ready(molecule)          → list[dict]  (list of bead dicts)
      beads_close(bead_id)           → None
      brain_recall(query)            → str         (recall text)
      brain_capture(text, type_)     → None
      dispatch(prompt, model, timeout_s) → dict    ({"tokens": int, "output": str})
      run_verify(cmd, timeout_s)     → int         (exit code)
    """

    beads_ready: Callable[[str], List[dict]]
    beads_close: Callable[[str], None]
    brain_recall: Callable[[str], str]
    brain_capture: Callable[[str, str], None]
    dispatch: Callable[[str, str, int], dict]
    run_verify: Callable[[str, int], int]


# ---------------------------------------------------------------------------
# Default (live) runners — call real subprocesses
# ---------------------------------------------------------------------------

def _live_beads_ready(molecule: str) -> List[dict]:
    """Return ready (unblocked, open) beads scoped to a molecule label.

    `beads ready` takes NO options (no -l/--json); only `beads list` does.
    So we intersect the global ready frontier (parsed from `beads ready`
    text) with the molecule's open beads (`beads list -l <label> --json`).
    ``molecule`` is a full bead label, e.g. "epic:scheduled-loop".
    """
    try:
        # 1. global ready frontier (text → set of ids)
        ready = subprocess.run(
            ["beads", "ready"], capture_output=True, text=True, timeout=30,
        )
        ready_ids = {
            b["id"] for b in _parse_beads_ready_text(ready.stdout) if b.get("id")
        }
        if not ready_ids:
            return []
        # 2. molecule's open beads (label filter → JSON)
        listed = subprocess.run(
            ["beads", "list", "-l", molecule, "--status", "open", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if listed.returncode != 0 or not listed.stdout.strip():
            return []
        molecule_beads = json.loads(listed.stdout)
        # 3. intersect: ready AND in molecule, preserving list order
        return [b for b in molecule_beads if b.get("id") in ready_ids]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("beads_ready failed: %s", exc)
        return []


def _parse_beads_ready_text(text: str) -> List[dict]:
    """Parse human-readable `beads ready` output into bead dicts.

    Lines look like: ○ fblai-xyz: Title  (priority: 2)
    """
    beads = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading status glyph
        if line and line[0] in ("○", "●", "◉", "◎"):
            line = line[1:].strip()
        # Parse "id: title" or "id: title (priority: N)"
        if ": " in line:
            bead_id, rest = line.split(": ", 1)
            # Extract priority if present
            priority = 2
            title = rest
            if "(priority:" in rest:
                title, paren = rest.rsplit("(priority:", 1)
                try:
                    priority = int(paren.rstrip(")").strip())
                except ValueError:
                    pass
            beads.append({
                "id": bead_id.strip(),
                "title": title.strip(),
                "priority": priority,
                "body": "",
                "labels": [],
            })
    return beads


def _live_beads_close(bead_id: str) -> None:
    """Call `beads close <id>`."""
    subprocess.run(
        ["beads", "close", bead_id],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _live_brain_recall(query: str) -> str:
    """Call open_brain.py --search."""
    open_brain = _SCRIPTS_DIR / "open_brain.py"
    try:
        result = subprocess.run(
            [sys.executable, str(open_brain), "--search", query, "--limit", "3"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("brain recall failed: %s", exc)
        return ""


def _live_brain_capture(text: str, type_: str) -> None:
    """Call open_brain.py --capture."""
    open_brain = _SCRIPTS_DIR / "open_brain.py"
    try:
        subprocess.run(
            [sys.executable, str(open_brain), "--capture", text, "--type", type_],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("brain capture failed: %s", exc)


def _live_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
    """Call `claude -p <prompt> --output-format json` (fresh process per iteration)."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        try:
            data = json.loads(result.stdout)
            tokens = data.get("usage", {}).get("output_tokens", 0)
            output = data.get("result", result.stdout)
            return {"tokens": tokens, "output": output}
        except json.JSONDecodeError:
            return {"tokens": 0, "output": result.stdout}
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise RuntimeError(f"dispatch failed: {exc}") from exc


def _live_run_verify(cmd: str, timeout_s: int) -> int:
    """Run the verification command, return its exit code."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=False,
            timeout=timeout_s,
            check=False,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.warning("verify command timed out after %ds", timeout_s)
        return 1


def make_live_runners() -> Runners:
    """Construct the real (live) Runners instance."""
    return Runners(
        beads_ready=_live_beads_ready,
        beads_close=_live_beads_close,
        brain_recall=_live_brain_recall,
        brain_capture=_live_brain_capture,
        dispatch=_live_dispatch,
        run_verify=_live_run_verify,
    )


# ---------------------------------------------------------------------------
# §1 helper: resolve bead's verify command (precedence order)
# ---------------------------------------------------------------------------

def resolve_verify_cmd(bead: dict, cli_verify_cmd: str) -> Optional[str]:
    """Resolve V with precedence: CLI flag > bead label verify:<cmd> > repo default.

    Returns None if unresolvable (triggers escalate).
    """
    if cli_verify_cmd:
        return cli_verify_cmd

    # Check bead labels for verify:<cmd>
    for label in bead.get("labels", []):
        if isinstance(label, str) and label.startswith("verify:"):
            cmd = label[len("verify:"):].strip()
            if cmd:
                return cmd

    # Repo default
    return _REPO_DEFAULT_VERIFY_CMD


# ---------------------------------------------------------------------------
# select_next — §2 (pure)
# ---------------------------------------------------------------------------

def select_next(ready: List[dict]) -> Optional[dict]:
    """Select the highest-priority ready bead.

    beads ready is pre-filtered (deps already satisfied), so we just pick
    the bead with the lowest priority number (highest precedence).

    Returns None if the list is empty.
    """
    if not ready:
        return None
    # Priority field: lower number = higher priority (1 > 2 > 3, etc.)
    return min(ready, key=lambda b: (b.get("priority", 99), b.get("id", "")))


# ---------------------------------------------------------------------------
# route_model — §1 (pure)
# ---------------------------------------------------------------------------

def route_model(bead: dict) -> str:
    """Route bead to a model tier based on its effort class label.

    Labels are checked for tier:<effort>. Falls back to 'sonnet' if unclear.
    Effort classes: design → opus, implement → sonnet, busywork → haiku.
    """
    labels = bead.get("labels", [])
    for label in labels:
        if isinstance(label, str):
            if label.startswith("tier:"):
                effort = label[len("tier:"):].strip().lower()
                if effort in LOOP_MODEL_MAP:
                    return LOOP_MODEL_MAP[effort]
            # Also accept direct effort labels
            for effort_class, model in LOOP_MODEL_MAP.items():
                if label.lower() == effort_class:
                    return model

    # Infer from title keywords
    title = bead.get("title", "").lower()
    if any(k in title for k in ("design", "architect", "plan", "spec")):
        return LOOP_MODEL_MAP["design"]
    if any(k in title for k in ("busywork", "cleanup", "rename", "trivial")):
        return LOOP_MODEL_MAP["busywork"]
    return LOOP_MODEL_MAP["implement"]


# ---------------------------------------------------------------------------
# compose_dispatch — §3 (pure function with self-check invariant)
# ---------------------------------------------------------------------------

def compose_dispatch(
    bead: dict,
    repo: str,
    branch: str,
    verify_cmd: str,
    *,
    recall_context: str = "",
) -> str:
    """Compose a gate-compliant dispatch prompt for this bead.

    Implements the D1 §3 template. MUST pass evaluate_dispatch(...).compliant.
    Raises ValueError if the composed prompt is non-compliant (bead → escalate).

    Key design constraints (from brain recall, bead brain-1781824198-c02169d4):
    - The output contract line uses "Report ..." NOT "Return a ..." to avoid
      false-positive termination criterion regex hits from _TERMINATION_RE's
      `return (only|a |the |...)` branch, which would conflate Rule 1 and Rule 3.
    - Acceptance block MUST be present to satisfy Rule 1 (termination criterion).
    - Paths reference only — no content pasting (satisfies Rule 2).
    """
    bead_id = bead.get("id", "unknown")
    title = bead.get("title", "")
    body = bead.get("body", "").strip()

    # Extract acceptance from body if present; inject default if absent
    acceptance_lines = _extract_acceptance(body, verify_cmd)

    # Build path hints from bead body (path references only, never content)
    discovered_paths = _extract_paths(body)
    if not discovered_paths:
        discovered_paths = "scripts/"   # fallback path hint so Rule-2 path-ref is present

    # Recall context block (included when non-empty)
    recall_block = ""
    if recall_context:
        recall_block = f"\nPrior context (from brain recall — informational only):\n{recall_context}\n"

    prompt = textwrap.dedent(f"""\
        You are working bead {bead_id} in {repo} (branch: {branch}).

        Objective: {title}

        Read these paths first (do NOT paste their contents back):
        {discovered_paths}
        {recall_block}
        Task:
        {body if body else title}

        Acceptance / termination criterion (you are DONE only when ALL hold):
        {acceptance_lines}
          - the change is committed
          - the verification command `{verify_cmd}` exits 0

        Report a one-paragraph summary of files changed and the command you ran with its result. Do NOT claim success you did not run.
    """).strip()

    # §3 Self-check invariant: raise if non-compliant
    verdict = evaluate_dispatch(prompt, mode="warn")
    if not verdict.get("compliant", False):
        raise ValueError(
            f"compose_dispatch produced a non-compliant prompt for bead {bead_id}. "
            f"missing={verdict['missing']} warnings={verdict['warnings']}\n"
            f"--- prompt ---\n{prompt}"
        )

    return prompt


def _extract_acceptance(body: str, verify_cmd: str) -> str:
    """Extract acceptance criteria from bead body, or generate a default."""
    lines = body.splitlines()
    acceptance_lines = []
    in_acceptance = False

    for line in lines:
        lower = line.lower().strip()
        if lower.startswith(("acceptance:", "done when:", "acceptance criteria:")):
            in_acceptance = True
            # Include the rest of this line after the colon
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                acceptance_lines.append(f"  - {rest}")
            continue
        if in_acceptance:
            # Continue until we hit a blank line or new section header
            if not line.strip():
                break
            if line.strip().startswith("#") or (line[0:1] not in (" ", "-", "*", "\t") and ":" in line):
                break
            stripped = line.strip().lstrip("-* ")
            if stripped:
                acceptance_lines.append(f"  - {stripped}")

    if acceptance_lines:
        return "\n".join(acceptance_lines)

    # Default: reference the verify command as the acceptance criterion
    return f"  - `{verify_cmd}` passes with exit 0"


def _extract_paths(body: str) -> str:
    """Extract path references from bead body text (paths only, never content)."""
    import re
    path_re = re.compile(
        r"[\w.\-]+/[\w.\-]+\.(?:py|ts|tsx|js|mjs|md|sql|sh|ps1|json|ya?ml)"
        r"|\b(?:scripts|src|docs|tests?|hooks)/[\w.\-/]+",
        re.IGNORECASE,
    )
    paths = path_re.findall(body)
    if not paths:
        return "scripts/"
    # Deduplicate preserving order
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return "\n".join(unique[:10])  # cap at 10 to keep prompt size sane


# ---------------------------------------------------------------------------
# close_if_verified — §4 (injectable runners)
# ---------------------------------------------------------------------------

def close_if_verified(
    bead: dict,
    verify_cmd: Optional[str],
    runners: Runners,
) -> IterationResult:
    """Run V; close bead ONLY on exit 0 (§4 close-gate contract).

    Precedence for V resolution is caller's responsibility — pass the resolved
    verify_cmd (or None to trigger escalation).

    Returns an IterationResult with outcome:
      "closed"    — V passed, bead closed, success captured to brain
      "failed"    — V failed, bead left open, failure captured to brain
      "escalated" — no V resolvable, bead left open, human needed
    """
    bead_id = bead.get("id", "unknown")
    title = bead.get("title", "")

    if not verify_cmd:
        # No verification command resolvable → escalate
        runners.brain_capture(
            f"Loop escalation: bead {bead_id} ({title}) has no resolvable verify command. "
            f"Human judgment required to close.",
            "pattern",
        )
        return IterationResult(
            bead_id=bead_id,
            outcome="escalated",
            tier=None,
            verify_exit=None,
            tokens_spent=0,
            note=f"Bead {bead_id} escalated: no verify command resolvable",
        )

    # Run V
    exit_code = runners.run_verify(verify_cmd, LOOP_VERIFY_TIMEOUT_S)

    if exit_code == 0:
        # Green: close the bead
        runners.beads_close(bead_id)
        runners.brain_capture(
            f"Loop success: bead {bead_id} ({title}) closed after verify command "
            f"`{verify_cmd}` exited 0.",
            "decision",
        )
        return IterationResult(
            bead_id=bead_id,
            outcome="closed",
            tier=None,
            verify_exit=0,
            tokens_spent=0,
            note=f"Bead {bead_id} closed — V exited 0",
        )
    else:
        # Red: leave open, capture failure data
        runners.brain_capture(
            f"Loop failure: bead {bead_id} ({title}) — verify command "
            f"`{verify_cmd}` exited {exit_code}. Bead left open.",
            "pattern",
        )
        return IterationResult(
            bead_id=bead_id,
            outcome="failed",
            tier=None,
            verify_exit=exit_code,
            tokens_spent=0,
            note=f"Bead {bead_id} NOT closed — V exited {exit_code}",
        )


# ---------------------------------------------------------------------------
# §5 Budget governor (in-runner checks)
# ---------------------------------------------------------------------------

def should_continue(summary: RunSummary, cfg: RunConfig) -> tuple[bool, str]:
    """Return (True, "") to continue, or (False, reason) to stop.

    Stop conditions (§5):
      - iterations >= max_iterations         → "max-iterations"
      - total_tokens >= budget_tokens        → "budget-exhausted"
      - consecutive_zero_close >= noprogress → "no-progress"
      (queue-empty is detected by select_next returning None → handled in run_loop)
    """
    if summary.iterations >= cfg.max_iterations:
        return False, "max-iterations"
    if summary.total_tokens >= cfg.budget_tokens:
        return False, "budget-exhausted"
    if summary.consecutive_zero_close >= LOOP_NOPROGRESS_K:
        return False, "no-progress"
    return True, ""


# ---------------------------------------------------------------------------
# run_iteration — §2 wiring (RECALL→SELECT→ROUTE→DISPATCH(gate)→EXECUTE→VERIFY→GATE+CAPTURE)
# ---------------------------------------------------------------------------

def run_iteration(cfg: RunConfig, runners: Runners) -> IterationResult:
    """Execute one full iteration of the loop.

    In dry-run mode: performs RECALL + SELECT + ROUTE + compose + gate check
    but does NOT call dispatch, run_verify, or beads_close.

    In live mode: full path.
    """
    # RECALL — pull prior context for the molecule
    recall_ctx = runners.brain_recall(f"bead molecule:{cfg.molecule} loop iteration")

    # SELECT — get ready beads and pick highest priority
    ready = runners.beads_ready(cfg.molecule)
    bead = select_next(ready)

    if bead is None:
        return IterationResult(
            bead_id=None,
            outcome="empty",
            tier=None,
            verify_exit=None,
            tokens_spent=0,
            note="No ready beads in queue",
        )

    bead_id = bead.get("id", "unknown")

    # ROUTE — pick model tier
    tier = route_model(bead)

    # Resolve verify command
    verify_cmd = resolve_verify_cmd(bead, cfg.verify_cmd)

    # DISPATCH(gate) — compose + self-check
    try:
        prompt = compose_dispatch(
            bead,
            cfg.repo,
            cfg.branch,
            verify_cmd or cfg.verify_cmd,
            recall_context=recall_ctx,
        )
    except ValueError as exc:
        # Gate blocked the prompt
        runners.brain_capture(
            f"Gate-blocked bead {bead_id}: compose_dispatch raised non-compliant. {exc}",
            "pattern",
        )
        return IterationResult(
            bead_id=bead_id,
            outcome="gate-blocked",
            tier=tier,
            verify_exit=None,
            tokens_spent=0,
            note=f"Bead {bead_id} gate-blocked: non-compliant prompt",
        )

    # DRY-RUN: print plan, perform no mutations
    if cfg.dry_run:
        _print_dry_run_plan(bead, tier, verify_cmd, prompt)
        return IterationResult(
            bead_id=bead_id,
            outcome="failed",   # dry-run does not close
            tier=tier,
            verify_exit=None,
            tokens_spent=0,
            note=f"[dry-run] Bead {bead_id} planned — no mutations performed",
        )

    # EXECUTE — dispatch the agent (fresh process, no growing conversation)
    try:
        dispatch_result = runners.dispatch(prompt, tier, LOOP_ITER_TIMEOUT_S)
    except Exception as exc:
        runners.brain_capture(
            f"Dispatch exception for bead {bead_id}: {exc}",
            "pattern",
        )
        return IterationResult(
            bead_id=bead_id,
            outcome="failed",
            tier=tier,
            verify_exit=None,
            tokens_spent=0,
            note=f"Bead {bead_id} dispatch raised: {exc}",
        )

    tokens = dispatch_result.get("tokens", 0)

    # VERIFY + GATE + CAPTURE — §4
    verify_result = close_if_verified(bead, verify_cmd, runners)

    # Carry tokens from this iteration back
    verify_result.tokens_spent = tokens
    verify_result.tier = tier

    return verify_result


# ---------------------------------------------------------------------------
# Run loop — accumulates IterationResults, applies governor
# ---------------------------------------------------------------------------

def run_loop(cfg: RunConfig, runners: Runners) -> RunSummary:
    """Execute the full loop until a stop condition fires."""
    summary = RunSummary(stop_reason="")

    while True:
        # Check budget governor BEFORE the iteration
        ok, reason = should_continue(summary, cfg)
        if not ok:
            summary.stop_reason = reason
            break

        try:
            result = run_iteration(cfg, runners)
        except Exception as exc:
            # Fail-open: an exception in one iteration does not crash the run loop
            # and never calls beads_close (result.outcome is never "closed" here)
            logger.exception("Unexpected error in iteration %d: %s", summary.iterations, exc)
            result = IterationResult(
                bead_id=None,
                outcome="failed",
                tier=None,
                verify_exit=None,
                tokens_spent=0,
                note=f"Iteration {summary.iterations} raised: {exc}",
            )

        summary.results.append(result)
        summary.iterations += 1
        summary.total_tokens += result.tokens_spent

        if result.outcome == "closed":
            summary.beads_closed += 1
            summary.consecutive_zero_close = 0
        elif result.outcome == "empty":
            summary.stop_reason = "queue-empty"
            break
        else:
            summary.consecutive_zero_close += 1

        # --once: exactly one real iteration then exit
        if cfg.once:
            summary.stop_reason = "once"
            break

    if not summary.stop_reason:
        summary.stop_reason = "unknown"

    return summary


# ---------------------------------------------------------------------------
# Dry-run plan printer
# ---------------------------------------------------------------------------

def _print_dry_run_plan(bead: dict, tier: str, verify_cmd: Optional[str], prompt: str) -> None:
    """Print the dry-run plan for one bead without performing any mutations."""
    bead_id = bead.get("id", "unknown")
    title = bead.get("title", "")
    resolved_v = verify_cmd or "(none — would escalate)"

    # Verify gate compliance for display
    verdict = evaluate_dispatch(prompt, mode="warn")
    gate_status = "COMPLIANT" if verdict.get("compliant") else "NON-COMPLIANT"
    if not verdict.get("compliant"):
        gate_status += f" (missing={verdict['missing']} warnings={verdict['warnings']})"

    print(f"\n[dry-run] Bead plan:")
    print(f"  bead_id      : {bead_id}")
    print(f"  title        : {title}")
    print(f"  tier         : {tier}")
    print(f"  gate_compliant: {gate_status}")
    print(f"  resolved_V   : {resolved_v}")
    print(f"\n[dry-run] Composed prompt ({len(prompt)} chars):")
    # Indent prompt for readability
    for line in prompt.splitlines():
        print(f"  {line}")
    print()


# ---------------------------------------------------------------------------
# CLI entry point (§6)
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loop_runner.py",
        description="Claude-side OptivAI Loop runner engine (T2, D1 §1–§7).",
    )
    parser.add_argument(
        "--molecule",
        required=True,
        help="Molecule label to filter ready beads (e.g. 'loop-runner').",
    )
    parser.add_argument(
        "--verify-cmd",
        default="",
        help="Verification command V. Falls back to bead label verify:<cmd> then repo default.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=LOOP_MAX_ITERATIONS,
        help=f"Hard iteration cap (default: {LOOP_MAX_ITERATIONS}).",
    )
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=LOOP_BUDGET_TOKENS,
        help=f"Hard output-token ceiling (default: {LOOP_BUDGET_TOKENS:,}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — no live dispatch, no beads closed, no brain-captures.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one real iteration then exit.",
    )
    parser.add_argument(
        "--repo",
        default="/Users/erato949/dev/optivai-claude-plugin",
        help="Repo path passed into the dispatch prompt.",
    )
    parser.add_argument(
        "--branch",
        default="perf/windows-optimization",
        help="Branch passed into the dispatch prompt.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI main. Returns exit code (0 = success / queue-empty / once, 1 = error)."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = RunConfig(
        molecule=args.molecule,
        repo=args.repo,
        branch=args.branch,
        verify_cmd=args.verify_cmd,
        max_iterations=args.max_iterations,
        budget_tokens=args.budget_tokens,
        dry_run=args.dry_run,
        once=args.once,
    )

    if cfg.dry_run:
        print(f"[dry-run] OptivAI Loop runner — molecule={cfg.molecule!r}")
        print(f"[dry-run] max_iterations={cfg.max_iterations}  budget_tokens={cfg.budget_tokens:,}")
        print(f"[dry-run] verify_cmd={cfg.verify_cmd!r}")
        print(f"[dry-run] No mutations will be performed.\n")

    runners = make_live_runners()
    summary = run_loop(cfg, runners)

    print(f"\n[loop] Run complete — stop_reason={summary.stop_reason!r}")
    print(f"[loop] iterations={summary.iterations}  beads_closed={summary.beads_closed}  "
          f"total_tokens={summary.total_tokens:,}")
    for r in summary.results:
        print(f"  iteration: bead={r.bead_id}  outcome={r.outcome}  "
              f"tier={r.tier}  verify_exit={r.verify_exit}  tokens={r.tokens_spent}")

    # Non-zero exit only on error-class stop reasons
    if summary.stop_reason in ("queue-empty", "once", "max-iterations",
                                "budget-exhausted", "no-progress"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
