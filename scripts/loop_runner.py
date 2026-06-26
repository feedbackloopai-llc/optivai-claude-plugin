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
import concurrent.futures
import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

# ---------------------------------------------------------------------------
# Resolve the scripts/ directory so we can import dispatch_gate from hooks/
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"

if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from dispatch_gate import evaluate_dispatch  # noqa: E402
from reconciler import reconcile as _reconcile, ReconcileAction  # noqa: E402

logger = logging.getLogger("loop_runner")

# ---------------------------------------------------------------------------
# §1 Named constants + env overrides
# ---------------------------------------------------------------------------

LOOP_MAX_ITERATIONS: int = int(os.environ.get("OPTIVAI_LOOP_MAX_ITERATIONS", "25"))
LOOP_BUDGET_TOKENS: int = int(os.environ.get("OPTIVAI_LOOP_BUDGET_TOKENS", "2000000"))
LOOP_NOPROGRESS_K: int = int(os.environ.get("OPTIVAI_LOOP_NOPROGRESS_K", "2"))
LOOP_MODEL_MAP: dict = {"design": "opus", "implement": "sonnet", "busywork": "haiku"}
# FABLE-CORE (fblai-3hf0c): tier:<model-name> routes directly to that model (the normalization fix - the live
# beads use tier:opus/sonnet/haiku, model names, which previously fell through to title inference). `fable` is
# the brightest tier but is NEVER routed directly - it is fail-closed gated below (needs fable-ready + no security).
ROUTABLE_MODEL_NAMES: frozenset = frozenset({"opus", "sonnet", "haiku"})
FABLE_TIER: str = "fable"
FABLE_READY_LABEL: str = "fable-ready"
# Security markers - a bead carrying ANY of these NEVER reaches fable, even if mis-certified fable-ready
# (defense-in-depth). Conservative by design: over-block (cost smarts=opus) is SAFE; under-block (leak security
# to Fable, which refuses/wastes it) is NOT. Substring match, so "authz"/"oauth"/"encryption"/"key-rotation" all
# block. This is a CONSERVATIVE SUPERSET of the security vocabulary (Gate-2 B1: a narrow set slipped 83% of it -
# `encryption`/`sandbox`/`rbac`/`credential` all reached Fable). It over-blocks some benign OptivAI beads
# (token-budget/agent-session/worktree-isolation) to OPUS - the spec's mandated safe direction. The isolation/
# hardening/tenant/sovereign tokens also catch the security EPICS + every future variant (Gate-2 I1). The label
# denylist can never be complete, so route_model ALSO scans the title/description (a NARROWER high-confidence
# set) - a second net. The PRIMARY control remains Harvey's deliberate `fable-ready` security-surface assessment.
FABLE_BLOCK_TOKENS: tuple = (
    # auth / access-control
    "security", "auth", "access-control", "access", "rbac", "permission", "privilege", "escalation",
    "credential", "secret", "token", "password", "session", "login", "sso", "saml", "oidc", "jwt", "mfa",
    # cryptography (the most-missed surface - "crypto" alone does NOT substring "encryption")
    "crypto", "encrypt", "decrypt", "cipher", "tls", "ssl", "pki", "hsm", "key",
    # cyber / vulnerability / offensive
    "cyber", "exploit", "vuln", "cve", "pentest", "malware", "xss", "csrf", "ssrf", "injection", "firewall",
    # isolation / sovereignty (the OptivAI security epics + FR2 no-egress)
    "sandbox", "isolation", "tenant", "hardening", "sovereign", "egress", "deas",
    # data protection / compliance
    "pii", "gdpr", "hipaa", "compliance",
)
# High-confidence tokens scanned in FREE-TEXT title/description (the second net). Narrower than the label set -
# only words unlikely to appear benignly in a title - so we do not over-block every bead whose title mentions a
# common word ("caching key", "user session"). Multi-char distinctive stems keep benign false-positives low.
FABLE_BLOCK_TEXT_TOKENS: tuple = (
    "security", "cyber", "exploit", "vulnerab", "vuln", "cve", "pentest", "malware",
    "xss", "csrf", "ssrf", "sql-injection", "rbac", "authenticat", "authoriz", "oauth", "saml", "oidc",
    "credential", "password", "encrypt", "decrypt", "cipher", "firewall", "access-control",
    "privilege", "sandbox-escape", "sovereign", "no-egress",
)
FABLE_BLOCK_EPICS: frozenset = frozenset(
    {"epic:harness-hardening", "epic:multi-tenant-isolation", "epic:per-user-isolation"}
)
LOOP_VERIFY_TIMEOUT_S: int = int(os.environ.get("OPTIVAI_LOOP_VERIFY_TIMEOUT_S", "900"))
LOOP_ITER_TIMEOUT_S: int = int(os.environ.get("OPTIVAI_LOOP_ITER_TIMEOUT_S", "1800"))
LOOP_MAX_WORKERS: int = int(os.environ.get("OPTIVAI_LOOP_MAX_WORKERS", "4"))

# P2 reconciler constants — mirror reconciler.py so callers need not import both
LOOP_STUCK_THRESHOLD_S: float = float(
    os.environ.get("OPTIVAI_LOOP_STUCK_THRESHOLD_S", "1800")
)
LOOP_SPAWNING_WINDOW_S: float = float(
    os.environ.get("OPTIVAI_LOOP_SPAWNING_WINDOW_S", "300")
)
LOOP_MAX_RESPAWNS: int = int(os.environ.get("OPTIVAI_LOOP_MAX_RESPAWNS", "1"))

# VB2 Refinery constants — batch-then-bisect + anti-starvation scoring.
# batch_max=1 (default at the RunConfig level) reproduces the VA0b serial path.
LOOP_BATCH_MAX: int = int(os.environ.get("OPTIVAI_LOOP_BATCH_MAX", "8"))
LOOP_REFINERY_ATTEMPTS_MAX: int = int(
    os.environ.get("OPTIVAI_LOOP_REFINERY_ATTEMPTS_MAX", "2")
)
# Anti-starvation scoring weights (pure orderer — see order_by_score).
#   W_AGE   : per-second wait weight (older verified candidates merge first)
#   W_RETRY : per-attempt weight (already-bounced branches are prioritized)
#   W_PRIO  : per-priority-rank weight (lower bead priority number = higher precedence)
# Tuned so a single retry or a few minutes of waiting outranks a fresh arrival.
LOOP_REFINERY_W_AGE: float = float(os.environ.get("OPTIVAI_LOOP_REFINERY_W_AGE", "1.0"))
LOOP_REFINERY_W_RETRY: float = float(
    os.environ.get("OPTIVAI_LOOP_REFINERY_W_RETRY", "120.0")
)
LOOP_REFINERY_W_PRIO: float = float(
    os.environ.get("OPTIVAI_LOOP_REFINERY_W_PRIO", "30.0")
)

# Default path for the shared loop state file (OBS2).
# Override via Runners.loop_state_path for testing.
LOOP_STATE_PATH: Path = Path.home() / ".claude" / "loop-state.json"

# Default verify command when none is specified (last resort, after --verify-cmd
# and the bead's verify:<cmd> label). Relative to the invocation cwd / worktree
# root so it carries no machine-specific path.
_REPO_DEFAULT_VERIFY_CMD = "cd scripts && python3 -m pytest -q"

# ---------------------------------------------------------------------------
# Worktree serialization lock (P1.2)
# Concurrent `git worktree add` calls race on the shared .git/config index.
# Workers may run dispatch in their own isolated worktrees, but worktree
# creation and teardown are serialized through this process-level lock.
# ---------------------------------------------------------------------------
_WORKTREE_LOCK: threading.Lock = threading.Lock()

# ---------------------------------------------------------------------------
# Merge slot (VA0b serial merge → VB2 Refinery)
# Merges from mayor/<bead_id> branches into the working branch must be
# serialized so concurrent workers never race on the same git index.
# VA0b held this only during one `git merge`.  VB2 promotes it to the merge
# slot: held around the whole batch-then-bisect cycle (refine()).  Only one
# batch cycle runs at a time; workers keep dispatching concurrently and only
# enqueue MergeCandidates — the Mayor main thread is the sole merger.
# ---------------------------------------------------------------------------
_MERGE_LOCK: threading.Lock = threading.Lock()

# ---------------------------------------------------------------------------
# MINOR 1 — bead_id validation
# Canonical bead IDs follow the pattern <namespace>-<hex> (e.g. "fblai-abc1").
# A bead_id starting with "-" is git arg-injection; one containing "..", spaces,
# "~", "^", or ":" makes an invalid git ref.  Validate before constructing any
# git branch name or worktree path from the raw id.
# ---------------------------------------------------------------------------
import re as _re

_BEAD_ID_RE = _re.compile(r'^(gz|fblai|optivai)-[a-z0-9]+$')


def _validate_bead_id(bead_id: str) -> bool:
    """Return True iff bead_id is safe to use in a git ref and a filesystem path.

    Accepted: ``<namespace>-<alphanum>`` where namespace is one of gz/fblai/optivai.
    Rejected: anything starting with '-', containing '..', whitespace, '~', '^', ':',
    or not matching the canonical ``^(gz|fblai|optivai)-[a-z0-9]+$`` pattern.
    """
    return bool(_BEAD_ID_RE.match(bead_id))


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
class WorkerResult:
    """Result returned by a Mayor worker thread.

    Workers are read-only with respect to bead state — they return this struct
    and the Mayor (main thread) performs all status mutations (the single-writer
    invariant).

    VA0b fields: the Mayor needs branch_name + worktree_path to perform the
    merge-on-pass and then tear down the worktree.  The worker NEVER merges —
    it only reports the coordinates.  Both are None when worktree isolation is
    not in use (backward-compatible).
    """

    bead_id: str
    dispatch_result: dict          # {"tokens": int, "output": str}
    verify_exit: Optional[int]     # exit code of V, or None on error
    error: Optional[Exception]     # set if the worker raised
    timed_out: bool = False        # set if the future timed out before completion
    # VA0b: coordinates for the Mayor's merge-on-pass path
    branch_name: Optional[str] = None    # "mayor/<bead_id>" branch created by worktree_manager
    worktree_path: Optional[str] = None  # path on disk; Mayor tears this down after merge
    # VA1: rate-limit backpressure.  True when the dispatch hit a provider
    # rate-limit (NOT a code failure).  The Mayor returns the bead to the ready
    # set (never burns it) and the governor pause-stops for a clean resume.
    rate_limited: bool = False


@dataclass
class WorkerHandle:
    """Tracks an in-flight worker submitted to the ThreadPoolExecutor."""

    bead_id: str
    future: "concurrent.futures.Future[WorkerResult]"
    model: str
    started_at: float              # monotonic time (passed in; no Date.now inside the runner)


@dataclass
class MayorSummary:
    """Totals accumulated by run_mayor_loop."""

    stop_reason: str = ""
    closed: int = 0
    failed: int = 0
    iterations: int = 0             # completion-rounds, not individual beads
    total_tokens: int = 0
    consecutive_zero_close: int = 0
    # VA1: set True once any worker reports a rate-limit.  Triggers the governor
    # pause-stop ("rate-limited").  rate_limited_beads counts how many beads were
    # returned to the ready set by backpressure (none were burned/failed).
    rate_limited: bool = False
    rate_limited_beads: int = 0


@dataclass
class MergeCandidate:
    """A V-passed worker branch awaiting integration by the Refinery (VB2).

    Workers never merge — on V-pass the Mayor enqueues one of these and the
    Refinery (refine(), Mayor main thread, single-writer) drains the queue.
    All fields the orderer needs (verified_at, attempts, priority) are carried
    on the candidate so order_by_score stays a pure function (no lookups).
    """

    bead_id: str
    branch_name: str               # "mayor/<bead_id>"
    worktree_path: Optional[str]   # Mayor tears down after the merge decision
    model: str                     # tier (for ledger)
    verified_at: float             # monotonic; passed in (no Date.now in the orderer)
    priority: int = 99             # bead priority (lower number = higher precedence)
    attempts: int = 0              # refinery re-implement count (anti-loop)


@dataclass
class RefineOutcome:
    """Result of refining one MergeCandidate (VB2).

    kind:
      "merged"      — branch landed on the working branch; close the bead.
      "reimplement" — conflict (textual or semantic); relabel + return to ready.
      "exhausted"   — re-implement cap reached; escalate (leave open, do not respawn).
    """

    candidate: MergeCandidate
    kind: str


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
    max_workers: int = 1           # default=1 preserves today's sequential behavior
    # P2 reconciler config
    stuck_threshold_s: float = LOOP_STUCK_THRESHOLD_S   # hung-detection threshold (seconds)
    spawning_window_s: float = LOOP_SPAWNING_WINDOW_S   # grace period for new workers (seconds)
    max_respawns: int = LOOP_MAX_RESPAWNS               # per-bead respawn cap (stops respawn loops)
    # VB2 Refinery config.  batch_max=1 reproduces the VA0b serial merge path
    # exactly (run_mayor_loop routes batch_max<=1 through the inline merge), so
    # every existing worktree/governor/rate-limit test stays green.
    batch_max: int = 1
    refinery_attempts_max: int = LOOP_REFINERY_ATTEMPTS_MAX


# ---------------------------------------------------------------------------
# Runners dataclass — injected side effects (mockable in tests)
# ---------------------------------------------------------------------------

@dataclass
class Runners:
    """Side-effecting callables, injectable for testing.

    Each callable signature:
      beads_ready(molecule)               → list[dict]  (list of bead dicts)
      beads_close(bead_id)                → None
      beads_update(bead_id, status)       → None        (Mayor marks in_progress before dispatch)
      brain_recall(query)                 → str         (recall text)
      brain_capture(text, type_)          → None
      dispatch(prompt, model, timeout_s)  → dict        ({"tokens": int, "output": str})
      run_verify(cmd, timeout_s)          → int         (exit code)

    loop_state_path: override the default LOOP_STATE_PATH for testing.
      When None, write_loop_state uses the module-level LOOP_STATE_PATH constant.

    P1.2 worktree isolation seam (both optional — absent means no isolation):
      worktree_manager(bead_id) → ContextManager[Optional[str]]
        A context manager that yields the path of an isolated git worktree for
        the given bead. Yields None when isolation is not applicable or available.
        worktree_manager handles locking (serializes git worktree add/remove) and
        cleanup on both normal exit and exception.
      dispatch_with_cwd(prompt, model, timeout_s, cwd) → dict
        Extended dispatch that accepts an optional working directory.  When
        present, _mayor_worker calls this instead of dispatch.  When absent
        (None), the worker falls back to dispatch (backward-compatible path).
    """

    beads_ready: Callable[[str], List[dict]]
    beads_close: Callable[[str], None]
    brain_recall: Callable[[str], str]
    brain_capture: Callable[[str, str], None]
    dispatch: Callable[[str, str, int], dict]
    run_verify: Callable[[str, int], int]
    loop_state_path: Optional[Path] = None
    # Mayor single-writer: set in_progress before submitting to pool
    beads_update: Optional[Callable[[str, str], None]] = None
    # P1.2 worktree isolation seam
    worktree_manager: Optional[Callable[[str], Any]] = None    # ContextManager[Optional[str]]
    dispatch_with_cwd: Optional[Callable[[str, str, int, Optional[str]], dict]] = None
    # P2 reconciler seam: judge(candidate, context) → "kill" | "respawn" | "wait"
    # Injected so tests use a fake; live path is a cheap-tier dispatch.
    # Fail-safe: reconcile treats None/raises as "wait" — never auto-kill on judge failure.
    judge: Optional[Callable[..., str]] = None
    # VA0b verify-in-worktree seam: run_verify_in_cwd(cmd, timeout_s, cwd) → int
    # When present, the worker calls this with the worktree cwd so V sees the
    # worker's committed changes.  Falls back to run_verify(cmd, timeout_s)
    # when None (backward-compatible for tests that don't exercise worktrees).
    run_verify_in_cwd: Optional[Callable[[str, int, str], int]] = None
    # VA0b named-branch worktree lifecycle seam (all three must be set together).
    # When worktree_create is present, _mayor_worker uses the new lifecycle:
    #   worktree_create(bead_id) → Optional[tuple[str, str]]  (path, branch_name)
    #     Returns None if git is unavailable or worktree add failed.
    #   worktree_teardown(path, branch_name) → None
    #     Called by the Mayor after merge decision; never by the worker itself.
    #   merge_branch(branch_name) → int
    #     Called by the Mayor under _MERGE_LOCK on V-pass.  Returns git exit code.
    # When absent, falls back to the old worktree_manager context-manager path.
    worktree_create: Optional[Callable[[str], Optional[tuple]]] = None
    worktree_teardown: Optional[Callable[[str, str], None]] = None
    merge_branch: Optional[Callable[[str], int]] = None
    # VB2 Refinery seams (batch-then-bisect).  All three drive the merge slot
    # from the Mayor main thread only — refine() is the sole caller.
    #   merge_batch(list[MergeCandidate]) → bool
    #     Sequentially `git merge --no-ff` each branch.  True iff ALL applied
    #     cleanly; False on the first textual conflict (working tree may be left
    #     mid-merge — git_reset cleans it up).
    #   git_snapshot(branch) → str
    #     Rollback point for the batch (rev-parse HEAD of the working branch).
    #   git_reset(snapshot) → None
    #     Atomic rollback: abort any in-progress merge + hard-reset to snapshot.
    # When absent, run_mayor_loop falls back to the VA0b inline merge_branch path.
    merge_batch: Optional[Callable[[List["MergeCandidate"]], bool]] = None
    git_snapshot: Optional[Callable[[str], str]] = None
    git_reset: Optional[Callable[[str], None]] = None
    # VB2: relabel a bead (conflict typing + refinery-attempts bookkeeping).
    #   beads_relabel(bead_id, label) → None
    # Mayor-only; fail-safe.  Absent → relabel is skipped (best-effort signal).
    beads_relabel: Optional[Callable[[str, str], None]] = None


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


def _live_beads_update(bead_id: str, status: str) -> None:
    """Call `beads update <id> --status <status>` (Mayor single-writer path)."""
    subprocess.run(
        ["beads", "update", bead_id, "--status", status],
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
    return _live_dispatch_with_cwd(prompt, model, timeout_s, cwd=None)


def _live_dispatch_with_cwd(
    prompt: str,
    model: str,
    timeout_s: int,
    cwd: Optional[str] = None,
) -> dict:
    """Call `claude -p <prompt> --output-format json`, optionally in a specific directory.

    ``cwd`` is the working directory for the subprocess (a per-worker git worktree
    path, or None to inherit the current process directory).

    VA0a: Strip ANTHROPIC_API_KEY from the subprocess env so that `claude -p`
    uses the Max-plan OAuth token (stored in ~/.claude/credentials) rather than
    a potentially depleted API key inherited from the parent process env.
    """
    # Build a clean env: inherit everything EXCEPT ANTHROPIC_API_KEY so that
    # `claude -p` falls back to the Max-plan OAuth credentials on disk.
    dispatch_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            cwd=cwd,
            env=dispatch_env,
        )
        # VA1: scan the full CLI response (stdout + stderr) for a rate-limit
        # signature.  A rate-limited dispatch is reported structurally via the
        # ``rate_limited`` key so the worker classifies it without re-scanning.
        blob = f"{result.stdout or ''}\n{result.stderr or ''}"
        try:
            data = json.loads(result.stdout)
            tokens = data.get("usage", {}).get("output_tokens", 0)
            output = data.get("result", result.stdout)
            rate_limited = bool(data.get("is_error")) and is_rate_limited(
                {"output": f"{output}\n{result.stderr or ''}"}, None
            )
            return {"tokens": tokens, "output": output, "rate_limited": rate_limited}
        except json.JSONDecodeError:
            return {
                "tokens": 0,
                "output": result.stdout,
                "rate_limited": is_rate_limited({"output": blob}, None),
            }
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


def _live_run_verify_in_cwd(cmd: str, timeout_s: int, cwd: str) -> int:
    """Run the verification command inside a specific directory, return its exit code.

    VA0b: V must run with cwd=<worktree path> so it sees the worker's committed
    changes in isolation rather than the stale working-branch checkout.
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=False,
            timeout=timeout_s,
            check=False,
            cwd=cwd,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.warning("verify command timed out after %ds (cwd=%s)", timeout_s, cwd)
        return 1


# ---------------------------------------------------------------------------
# VA1 — Rate-limit classifier (pure; shared by live dispatch + worker)
# ---------------------------------------------------------------------------

# Case-insensitive substrings that mark a provider rate-limit / usage-limit.
# A rate-limit is a backpressure signal — NOT a code failure.  Keep this list
# focused: a false positive only pauses the run (the bead is never burned and a
# resume re-dispatches it), but over-broad matching would pause needlessly.
_RATE_LIMIT_SIGNATURES = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "ratelimit",
    "429",
    "too many requests",
    "usage limit",          # Max-plan: "Claude AI usage limit reached"
    "quota exceeded",
)


def is_rate_limited(
    dispatch_result: Optional[dict],
    error: Optional[BaseException],
) -> bool:
    """Classify a dispatch outcome as RATE_LIMITED (VA1).

    RATE_LIMITED is treated DISTINCTLY from FAILED: a True result means the
    Mayor returns the bead to the ready set (never burns it) and the governor
    pause-stops so the loop resumes cleanly once the rate-limit window clears.

    Detection sources (any one is sufficient):
      1. An explicit truthy ``rate_limited`` key on the dispatch_result dict —
         the structured signal set by the live dispatch when it can detect the
         limit from the CLI response.  This is the primary, lowest-noise path.
      2. A rate-limit signature substring in the dispatch_result ``output`` text.
      3. A rate-limit signature substring in ``str(error)`` (a raised exception,
         e.g. an API 429 surfaced as a RuntimeError).
    """
    if dispatch_result is not None:
        if dispatch_result.get("rate_limited"):
            return True
        text = str(dispatch_result.get("output", "") or "").lower()
        if any(sig in text for sig in _RATE_LIMIT_SIGNATURES):
            return True
    if error is not None:
        etext = str(error).lower()
        if any(sig in etext for sig in _RATE_LIMIT_SIGNATURES):
            return True
    return False


@contextlib.contextmanager
def _live_worktree_manager(bead_id: str) -> Iterator[Optional[str]]:
    """Create an isolated git worktree for *bead_id*, yield its path, then remove it.

    LEGACY CONTEXT-MANAGER PATH — kept for backward compatibility with tests and
    callers that do not use the VA0b named-branch lifecycle.  New production code
    uses _live_worktree_create / _live_worktree_teardown instead.

    Serialized through _WORKTREE_LOCK so concurrent workers never race on
    `git worktree add` (which modifies the shared .git/config and packed-refs).
    The lock is held only during creation and teardown; the dispatched subagent
    runs inside the worktree without holding the lock, so workers run truly in
    parallel while isolation is maintained.

    Yields None if:
    - bead_id does not match the canonical pattern (MINOR 1 guard)
    - not inside a git repository (git rev-parse fails)
    - git worktree add fails for any reason

    On either normal or exceptional exit, the worktree is removed (serialized).
    """
    # MINOR 1: reject bead_ids that would produce invalid git refs or paths.
    if not _validate_bead_id(bead_id):
        logger.warning(
            "Worktree creation skipped: bead_id %r does not match canonical pattern "
            r"^(gz|fblai|optivai)-[a-z0-9]+$ — refusing to pass to git",
            bead_id,
        )
        yield None
        return

    worktree_path: Optional[str] = None
    try:
        # Discover the repo root (needed for `git worktree add`)
        try:
            rev_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if rev_result.returncode != 0:
                yield None
                return
            repo_root = rev_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            yield None
            return

        # Sanitize bead_id for use as a directory name component
        safe_id = bead_id.replace("/", "_").replace("\\", "_")
        wt_dir = os.path.join(
            tempfile.gettempdir(),
            f"mayor-wt-{safe_id}-{threading.get_ident()}",
        )

        # Serialize worktree creation
        with _WORKTREE_LOCK:
            add_result = subprocess.run(
                ["git", "worktree", "add", "--detach", wt_dir],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=repo_root,
            )
            if add_result.returncode != 0:
                logger.warning(
                    "git worktree add failed for bead %s: %s",
                    bead_id,
                    add_result.stderr.strip(),
                )
                yield None
                return
            worktree_path = wt_dir

        yield worktree_path

    finally:
        if worktree_path is not None:
            # Serialize worktree removal (matches the add serialization)
            with _WORKTREE_LOCK:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", worktree_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )


def _discover_repo_root() -> Optional[str]:
    """Return the git repo root for the current working directory, or None."""
    try:
        rev_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if rev_result.returncode != 0:
            return None
        return rev_result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _live_worktree_create(bead_id: str) -> Optional[tuple]:
    """VA0b: Create a named-branch worktree for *bead_id*.

    Creates worktree on branch ``mayor/<bead_id>`` (not detached) so the
    worker's commits are reachable for merge.

    Returns ``(worktree_path: str, branch_name: str)`` on success, or None if
    git is unavailable or `git worktree add` fails.

    Serialized through _WORKTREE_LOCK — safe for concurrent workers.
    """
    # MINOR 1: reject bead_ids that would produce invalid git refs or paths.
    # A bead_id starting with '-' is git arg-injection; "..", spaces, "~", "^", ":"
    # make invalid refs.  Only the canonical pattern is safe.
    if not _validate_bead_id(bead_id):
        logger.warning(
            "Worktree creation skipped: bead_id %r does not match canonical pattern "
            r"^(gz|fblai|optivai)-[a-z0-9]+$ — refusing to pass to git",
            bead_id,
        )
        return None

    repo_root = _discover_repo_root()
    if repo_root is None:
        return None

    # Sanitize bead_id for branch name and directory
    safe_id = bead_id.replace("/", "_").replace("\\", "_")
    branch_name = f"mayor/{bead_id}"
    wt_dir = os.path.join(
        tempfile.gettempdir(),
        f"mayor-wt-{safe_id}-{threading.get_ident()}",
    )

    with _WORKTREE_LOCK:
        add_result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, wt_dir, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if add_result.returncode != 0:
            logger.warning(
                "git worktree add (named branch) failed for bead %s: %s",
                bead_id,
                add_result.stderr.strip(),
            )
            return None

    return (wt_dir, branch_name)


def _live_worktree_teardown(worktree_path: str, branch_name: str) -> None:
    """VA0b: Remove a worktree and delete its branch.

    Always called by the Mayor after merge decision — never by the worker.
    Serialized through _WORKTREE_LOCK to match creation serialization.
    Fail-safe: errors are logged and swallowed (teardown must not crash the Mayor).
    """
    repo_root = _discover_repo_root()

    with _WORKTREE_LOCK:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=repo_root or ".",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("worktree teardown (remove) failed for %s: %s", worktree_path, exc)

        # Delete the named branch so it doesn't accumulate
        if repo_root and branch_name:
            try:
                subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=repo_root,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning("worktree branch delete failed for %s: %s", branch_name, exc)


def _live_merge_worktree_branch(branch_name: str) -> int:
    """VA0b: Merge *branch_name* into the current working branch.

    Called by the Mayor (serialized under _MERGE_LOCK) after V passes.
    Returns the git merge exit code (0 = success, non-zero = conflict/error).
    Fail-safe: any subprocess error is logged and returns 1.
    """
    repo_root = _discover_repo_root()
    if repo_root is None:
        logger.warning("merge_worktree_branch: not in a git repo, skipping merge")
        return 1

    try:
        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name,
             "-m", f"Mayor: merge {branch_name} (V passed)"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repo_root,
        )
        if result.returncode != 0:
            logger.warning(
                "merge %s failed (exit %d): %s",
                branch_name, result.returncode, result.stderr.strip(),
            )
        return result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("merge_worktree_branch raised: %s", exc)
        return 1


def _live_git_snapshot(branch: str) -> str:
    """VB2: Return the current HEAD sha of the working branch (rollback point).

    Returns "" if git is unavailable — refine() treats an empty snapshot as
    "no rollback possible" and falls back conservatively.
    """
    repo_root = _discover_repo_root()
    if repo_root is None:
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git_snapshot raised: %s", exc)
        return ""


def _live_git_reset(snapshot: str) -> None:
    """VB2: Atomic rollback of a failed batch to *snapshot*.

    Aborts any in-progress merge, then hard-resets to the snapshot sha so the
    working branch is byte-identical to its pre-batch state.  Fail-safe.
    """
    repo_root = _discover_repo_root()
    if repo_root is None or not snapshot:
        return
    # Abort any in-progress merge first (ignore errors — there may be none).
    try:
        subprocess.run(
            ["git", "merge", "--abort"],
            capture_output=True, text=True, timeout=30, cwd=repo_root,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git_reset: merge --abort raised: %s", exc)
    try:
        subprocess.run(
            ["git", "reset", "--hard", snapshot],
            capture_output=True, text=True, timeout=30, cwd=repo_root,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git_reset: reset --hard raised: %s", exc)


def _live_merge_batch(batch: List["MergeCandidate"]) -> bool:
    """VB2: Sequentially `git merge --no-ff` each candidate's branch.

    Returns True iff every branch applied cleanly.  On the first non-zero merge
    (textual conflict) returns False immediately — the caller (refine) resets to
    the pre-batch snapshot, so a half-applied batch never persists.
    """
    for c in batch:
        if _live_merge_worktree_branch(c.branch_name) != 0:
            return False
    return True


def _live_beads_relabel(bead_id: str, label: str) -> None:
    """VB2: Apply a label to a bead (`beads label <id> <label>`). Fail-safe."""
    try:
        subprocess.run(
            ["beads", "label", bead_id, label],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("beads_relabel failed for %s: %s", bead_id, exc)


def make_live_runners() -> Runners:
    """Construct the real (live) Runners instance.

    VA0b: wires the named-branch worktree lifecycle (worktree_create /
    worktree_teardown / merge_branch) alongside the legacy worktree_manager
    (kept for backward compat).  When worktree_create is present,
    _mayor_worker uses the VA0b path; otherwise it falls back to the context-
    manager path.
    """
    return Runners(
        beads_ready=_live_beads_ready,
        beads_close=_live_beads_close,
        beads_update=_live_beads_update,
        brain_recall=_live_brain_recall,
        brain_capture=_live_brain_capture,
        dispatch=_live_dispatch,
        run_verify=_live_run_verify,
        run_verify_in_cwd=_live_run_verify_in_cwd,
        worktree_manager=_live_worktree_manager,
        dispatch_with_cwd=_live_dispatch_with_cwd,
        # VA0b named-branch worktree lifecycle
        worktree_create=_live_worktree_create,
        worktree_teardown=_live_worktree_teardown,
        merge_branch=_live_merge_worktree_branch,
        # VB2 Refinery seams (batch-then-bisect)
        merge_batch=_live_merge_batch,
        git_snapshot=_live_git_snapshot,
        git_reset=_live_git_reset,
        beads_relabel=_live_beads_relabel,
    )


# ---------------------------------------------------------------------------
# OBS2 — loop state file helpers
# ---------------------------------------------------------------------------

def write_loop_state(state: dict, path: Path) -> None:
    """Write loop state atomically to *path*.

    Uses a temporary file in the same directory as *path* + ``os.replace``
    so a reader never sees a half-written file.  Fail-open: any error is
    logged to stderr and silently swallowed — this function MUST NOT raise.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then atomically replace
        fd, tmp_path_str = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            os.replace(tmp_path_str, path)
        except Exception:
            # Clean up orphaned temp file if replace failed
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise
    except Exception as exc:
        print(f"[loop-state] write failed (non-fatal): {exc}", file=sys.stderr)


def _build_state(
    summary: RunSummary,
    cfg: RunConfig,
    *,
    status: str,
    last_bead: Optional[str],
    last_outcome: Optional[str],
    stop_reason: Optional[str],
    active: bool = True,
) -> dict:
    """Build the §1 schema dict for the loop state file (single-worker / run_loop path).

    Fields:
      harness       — always "claude" for this runner
      active        — True while looping; False on termination
      molecule      — the scoped label from cfg
      iteration     — current iteration count (from summary)
      max_iterations— from cfg
      closed        — cumulative beads closed
      failed        — cumulative non-closed outcomes (total iterations - closed)
      tokens        — cumulative output tokens
      last_bead     — most recent bead id worked (or None)
      last_outcome  — most recent outcome string (or None)
      status        — "running"|"dry-run"|"done"|"stopped"
      stop_reason   — why the run stopped, or None while running
      updated_at    — float unix epoch (time.time())
    """
    failed_count = summary.iterations - summary.beads_closed
    return {
        "harness": "claude",
        "active": active,
        "molecule": cfg.molecule,
        "iteration": summary.iterations,
        "max_iterations": cfg.max_iterations,
        "closed": summary.beads_closed,
        "failed": failed_count,
        "tokens": summary.total_tokens,
        "last_bead": last_bead,
        "last_outcome": last_outcome,
        "status": status,
        "stop_reason": stop_reason,
        "updated_at": time.time(),
    }


def _build_mayor_state(
    summary: MayorSummary,
    cfg: RunConfig,
    *,
    status: str,
    stop_reason: Optional[str],
    active_handles: "Dict[str, WorkerHandle]",
    recovery_blocked: "Set[str]",
    now_monotonic: float,
    active: bool = True,
) -> dict:
    """Build the loop state dict for the Mayor multi-worker path (P3.1).

    Extends the §1 schema with Mayor-specific fields:

      active_workers — list of {bead_id, model, runtime_s} for in-flight workers
      capacity       — {max, active, recovery_blocked, free}

    Backward compat: ``active_workers`` is present only in Mayor states. The
    statusline checks for its presence to select the multi-worker render path.
    """
    worker_list = [
        {
            "bead_id": h.bead_id,
            "model": h.model,
            "runtime_s": round(now_monotonic - h.started_at, 1),
        }
        for h in active_handles.values()
    ]
    n_active = len(active_handles)
    n_recovery = len(recovery_blocked)
    capacity = {
        "max": cfg.max_workers,
        "active": n_active,
        "recovery_blocked": n_recovery,
        "free": cfg.max_workers - n_active - n_recovery,
    }
    total = summary.closed + summary.failed
    return {
        "harness": "claude",
        "active": active,
        "molecule": cfg.molecule,
        "iteration": summary.iterations,
        "max_iterations": cfg.max_iterations,
        "closed": summary.closed,
        "failed": summary.failed,
        "tokens": summary.total_tokens,
        "last_bead": None,
        "last_outcome": None,
        "status": status,
        "stop_reason": stop_reason,
        "updated_at": time.time(),
        # Mayor-specific fields
        "active_workers": worker_list,
        "capacity": capacity,
    }


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

def _bead_is_security_marked(bead: dict) -> bool:
    """True if the bead carries ANY security surface (FABLE-CORE defense-in-depth; never Fable on security).

    Two nets (Gate-2 B1/I1): (1) a broad substring token match on every LABEL (so `authz`/`oauth`/`encryption`/
    `key-rotation` all trip) PLUS the 3 named security epics; the isolation/hardening/tenant/sovereign tokens
    also catch epic variants. (2) a NARROWER high-confidence scan of the free-text title+description - because a
    label denylist can never be complete (a `perf-refactor`-labelled bead that rewrites the auth cache slips the
    labels). Over-blocking is intentional: it costs smarts (Opus), never safety.
    """
    for raw in bead.get("labels", []):
        if not isinstance(raw, str):
            continue
        label = raw.strip().lower()
        if label in FABLE_BLOCK_EPICS:
            return True
        if any(tok in label for tok in FABLE_BLOCK_TOKENS):
            return True
    # Second net: high-confidence tokens in the free text (title + description).
    text = f"{bead.get('title', '')} {bead.get('description', '')} {bead.get('body', '')}".lower()
    if any(tok in text for tok in FABLE_BLOCK_TEXT_TOKENS):
        return True
    return False


def route_model(bead: dict) -> str:
    """Route a bead to a model by its tier: label. FABLE-CORE (fblai-3hf0c) fail-closed routing.

    Precedence:
      1. tier:fable -> FABLE only if `fable-ready` present AND no security marker; else DOWNGRADE to opus
         (deterministic + logged - we never let Fable refuse-and-fallback). Fail-closed: no fable-ready => opus.
      2. tier:opus|sonnet|haiku -> that model directly (the normalization fix; these are the live beads).
      3. tier:design|implement|busywork -> LOOP_MODEL_MAP (effort classes).
      4. bare effort-class label (design/implement/busywork) -> LOOP_MODEL_MAP.
      5. else -> title-keyword inference (unchanged), defaulting to implement (sonnet).
    """
    labels = [l for l in bead.get("labels", []) if isinstance(l, str)]
    tier = None
    for raw in labels:
        s = raw.strip().lower()
        if s.startswith("tier:"):
            tier = s[len("tier:"):].strip()
            break

    # 1. FABLE fail-closed gate (the crux) - a security-marked or non-certified bead NEVER reaches fable.
    if tier == FABLE_TIER:
        if _bead_is_security_marked(bead):
            logger.info(
                "[route_model] tier:fable -> opus (security-marked; never Fable on security) bead=%s",
                bead.get("id", "?"),
            )
            return "opus"
        if FABLE_READY_LABEL in [l.strip().lower() for l in labels]:
            return "fable"
        logger.info(
            "[route_model] tier:fable -> opus (no fable-ready certification; fail-closed) bead=%s",
            bead.get("id", "?"),
        )
        return "opus"

    # 2. tier:<model-name> routes directly (opus/sonnet/haiku) - the normalization fix.
    if tier in ROUTABLE_MODEL_NAMES:
        return tier

    # 3. tier:<effort-class> via LOOP_MODEL_MAP.
    if tier in LOOP_MODEL_MAP:
        return LOOP_MODEL_MAP[tier]

    # 4. bare effort-class labels (design/implement/busywork).
    for raw in labels:
        if raw.strip().lower() in LOOP_MODEL_MAP:
            return LOOP_MODEL_MAP[raw.strip().lower()]

    # 5. Infer from title keywords (unchanged).
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

def should_continue(
    summary: RunSummary,
    cfg: RunConfig,
    active: Optional[Dict[str, "WorkerHandle"]] = None,
    recovery_blocked: Optional[Set[str]] = None,
) -> tuple[bool, str]:
    """Return (True, "") to continue, or (False, reason) to stop.

    Stop conditions (§5), extended for concurrency-aware Mayor mode:
      - iterations >= max_iterations                                → "max-iterations"
      - total_tokens >= budget_tokens                               → "budget-exhausted"
      - consecutive_zero_close >= noprogress AND active is empty    → "no-progress"
      - len(recovery_blocked) >= max_workers AND active is empty    → "capacity-exhausted-by-stuck-workers"
      (queue-empty is detected by caller when no work can be dispatched)

    The active/recovery_blocked parameters are optional for backward compatibility
    with sequential run_loop which calls this without them.
    """
    _active = active or {}
    _recovery = recovery_blocked or set()

    if summary.iterations >= cfg.max_iterations:
        return False, "max-iterations"
    if summary.total_tokens >= cfg.budget_tokens:
        return False, "budget-exhausted"
    # no-progress: only fire when no workers are running (a running worker is progress)
    if summary.consecutive_zero_close >= LOOP_NOPROGRESS_K and not _active:
        return False, "no-progress"
    # capacity-exhausted: all slots occupied by crashed workers, nothing running, nothing ready
    if (
        _recovery
        and len(_recovery) >= cfg.max_workers
        and not _active
    ):
        return False, "capacity-exhausted-by-stuck-workers"
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
# Mayor worker — runs in a thread; NEVER mutates bead status
# ---------------------------------------------------------------------------

def _mayor_worker(bead: dict, cfg: RunConfig, runners: Runners) -> WorkerResult:
    """Execute dispatch + verify for one bead inside a worker thread.

    Single-writer invariant: this function MUST NOT call beads_close, beads_update,
    or any other status mutation. It composes and dispatches the prompt, runs the
    verify command, and returns a WorkerResult. The Mayor (main thread) reads the
    result and performs all status writes.

    Running verify inside the worker is correct — it is a test command that writes
    only to the worker's isolated environment, not to bead state. This keeps the
    slow path parallel while status writes remain serialized to the main thread.

    P1.2 worktree isolation: when runners.worktree_manager is provided, the dispatch
    runs inside a dedicated git worktree (via runners.dispatch_with_cwd). The worktree
    lifecycle is managed entirely here — creation before dispatch, teardown after the
    worker finishes (including on error). Worktree create/teardown serialize through
    _WORKTREE_LOCK (inside the worktree_manager); the dispatch itself runs without
    holding the lock so workers run truly in parallel.
    """
    bead_id = bead.get("id", "unknown")
    tier = route_model(bead)
    verify_cmd = resolve_verify_cmd(bead, cfg.verify_cmd)

    # DISPATCH(gate) — compose + self-check
    try:
        recall_ctx = runners.brain_recall(f"bead molecule:{cfg.molecule} loop iteration")
        prompt = compose_dispatch(
            bead,
            cfg.repo,
            cfg.branch,
            verify_cmd or cfg.verify_cmd,
            recall_context=recall_ctx,
        )
    except ValueError as exc:
        runners.brain_capture(
            f"Mayor gate-blocked bead {bead_id}: compose_dispatch raised non-compliant. {exc}",
            "pattern",
        )
        return WorkerResult(
            bead_id=bead_id,
            dispatch_result={"tokens": 0, "output": ""},
            verify_exit=1,
            error=exc,
        )

    # ------------------------------------------------------------------
    # EXECUTE — dispatch the subagent inside an isolated worktree when available.
    #
    # VA0b (named-branch lifecycle): when runners.worktree_create is set, use
    # the new lifecycle where the Mayor controls teardown after merge decision.
    # The worker creates the worktree, dispatches, runs V in the worktree, then
    # RETURNS without tearing down — the Mayor tears down after merge-or-discard.
    #
    # Legacy (context-manager): when worktree_create is None but worktree_manager
    # is set, fall back to the old path (context manager handles creation +
    # teardown, backward-compatible with existing tests).
    #
    # No isolation: fall back to plain dispatch (backward-compatible).
    # ------------------------------------------------------------------

    if runners.worktree_create is not None:
        # ---- VA0b named-branch path ----
        wt_info = runners.worktree_create(bead_id)
        if wt_info is not None:
            wt_path, wt_branch = wt_info
        else:
            wt_path, wt_branch = None, None

        # Choose dispatch callable
        if wt_path is not None and runners.dispatch_with_cwd is not None:
            dispatch_fn = lambda p, m, t: runners.dispatch_with_cwd(p, m, t, wt_path)
        else:
            dispatch_fn = runners.dispatch

        try:
            dispatch_result = dispatch_fn(prompt, tier, LOOP_ITER_TIMEOUT_S)
        except Exception as exc:
            # VA1: a rate-limit raised as an exception is backpressure, not a
            # crash — flag it so the Mayor returns the bead to the ready set.
            if is_rate_limited(None, exc):
                return WorkerResult(
                    bead_id=bead_id,
                    dispatch_result={"tokens": 0, "output": str(exc)},
                    verify_exit=None,
                    error=None,
                    rate_limited=True,
                    branch_name=wt_branch,
                    worktree_path=wt_path,
                )
            runners.brain_capture(
                f"Mayor dispatch exception for bead {bead_id}: {exc}",
                "pattern",
            )
            return WorkerResult(
                bead_id=bead_id,
                dispatch_result={"tokens": 0, "output": ""},
                verify_exit=None,
                error=exc,
                branch_name=wt_branch,
                worktree_path=wt_path,
            )

        # VA1: rate-limit detected in the dispatch response — skip V (no real
        # work happened) and let the Mayor return the bead to the ready set.
        if is_rate_limited(dispatch_result, None):
            return WorkerResult(
                bead_id=bead_id,
                dispatch_result=dispatch_result,
                verify_exit=None,
                error=None,
                rate_limited=True,
                branch_name=wt_branch,
                worktree_path=wt_path,
            )

        # VERIFY — run V IN the worktree so it sees the worker's committed code
        if verify_cmd:
            try:
                if wt_path is not None and runners.run_verify_in_cwd is not None:
                    exit_code = runners.run_verify_in_cwd(
                        verify_cmd, LOOP_VERIFY_TIMEOUT_S, wt_path
                    )
                else:
                    exit_code = runners.run_verify(verify_cmd, LOOP_VERIFY_TIMEOUT_S)
            except Exception as exc:
                return WorkerResult(
                    bead_id=bead_id,
                    dispatch_result=dispatch_result,
                    verify_exit=None,
                    error=exc,
                    branch_name=wt_branch,
                    worktree_path=wt_path,
                )
        else:
            exit_code = None  # No verify cmd — Mayor will escalate

        # Worker returns WITHOUT tearing down.  The Mayor merges (on V-pass) or
        # discards (on V-fail) and then tears down via runners.worktree_teardown.
        return WorkerResult(
            bead_id=bead_id,
            dispatch_result=dispatch_result,
            verify_exit=exit_code,
            error=None,
            branch_name=wt_branch,
            worktree_path=wt_path,
        )

    else:
        # ---- Legacy context-manager path (unchanged) ----
        wt_ctx: Any = (
            runners.worktree_manager(bead_id)
            if runners.worktree_manager is not None
            else contextlib.nullcontext(None)
        )

        try:
            with wt_ctx as worktree_path:
                # Choose the dispatch callable: cwd-aware when worktree is active
                if worktree_path is not None and runners.dispatch_with_cwd is not None:
                    dispatch_fn = lambda p, m, t: runners.dispatch_with_cwd(p, m, t, worktree_path)
                else:
                    dispatch_fn = runners.dispatch

                try:
                    dispatch_result = dispatch_fn(prompt, tier, LOOP_ITER_TIMEOUT_S)
                except Exception as exc:
                    # VA1: rate-limit raised as an exception → backpressure, not a crash.
                    if is_rate_limited(None, exc):
                        return WorkerResult(
                            bead_id=bead_id,
                            dispatch_result={"tokens": 0, "output": str(exc)},
                            verify_exit=None,
                            error=None,
                            rate_limited=True,
                        )
                    runners.brain_capture(
                        f"Mayor dispatch exception for bead {bead_id}: {exc}",
                        "pattern",
                    )
                    return WorkerResult(
                        bead_id=bead_id,
                        dispatch_result={"tokens": 0, "output": ""},
                        verify_exit=None,
                        error=exc,
                    )

                # VA1: rate-limit detected in the dispatch response — skip V.
                if is_rate_limited(dispatch_result, None):
                    return WorkerResult(
                        bead_id=bead_id,
                        dispatch_result=dispatch_result,
                        verify_exit=None,
                        error=None,
                        rate_limited=True,
                    )

                # VERIFY — run V in the worker (reads env, does not mutate bead state)
                if verify_cmd:
                    try:
                        exit_code = runners.run_verify(verify_cmd, LOOP_VERIFY_TIMEOUT_S)
                    except Exception as exc:
                        return WorkerResult(
                            bead_id=bead_id,
                            dispatch_result=dispatch_result,
                            verify_exit=None,
                            error=exc,
                        )
                else:
                    exit_code = None  # No verify cmd — Mayor will escalate

        except Exception as exc:
            # Catch any unexpected exception from the worktree lifecycle itself
            runners.brain_capture(
                f"Mayor worktree lifecycle exception for bead {bead_id}: {exc}",
                "pattern",
            )
            return WorkerResult(
                bead_id=bead_id,
                dispatch_result={"tokens": 0, "output": ""},
                verify_exit=None,
                error=exc,
            )

        return WorkerResult(
            bead_id=bead_id,
            dispatch_result=dispatch_result,
            verify_exit=exit_code,
            error=None,
        )


# ---------------------------------------------------------------------------
# pick — deterministic slot-filling selection (pure)
# ---------------------------------------------------------------------------

def _pick(
    ready: List[dict],
    free: int,
    exclude: Set[str],
) -> List[dict]:
    """Select up to `free` beads from `ready`, skipping any in `exclude`.

    Preserves the select_next ordering (priority then id).
    """
    candidates = [b for b in ready if b.get("id") not in exclude]
    # Sort by (priority, id) — same key as select_next
    candidates.sort(key=lambda b: (b.get("priority", 99), b.get("id", "")))
    return candidates[:free]


# ---------------------------------------------------------------------------
# Mayor ledger helper (P3.2) — fail-safe brain_capture wrapper
# ---------------------------------------------------------------------------

def _mayor_capture(runners: Runners, text: str, type_: str) -> None:
    """Fail-safe wrapper for runners.brain_capture in the Mayor main loop.

    The Mayor loop MUST NOT crash because the brain subsystem is unavailable.
    Any exception from brain_capture is logged at WARNING level and swallowed.
    This wrapper is used for ALL Mayor-side brain_capture calls (both the
    existing diagnostic captures and the new P3.2 ledger events).
    """
    try:
        runners.brain_capture(text, type_)
    except Exception as exc:
        logger.warning("Mayor: brain_capture failed (non-fatal): %s", exc)


def _ledger_capture(
    runners: Runners,
    *,
    action: str,
    bead_id: str,
    molecule: str,
    tier: Optional[str],
    extra: str = "",
) -> None:
    """Emit a Mayor-side task-state-transition ledger event via brain_capture.

    Covers three transitions: dispatch, verify-result (pass or fail), close.
    Fail-safe: if brain_capture raises (unavailable, timeout, etc.) the
    exception is logged and silently swallowed — the Mayor loop MUST NOT
    crash because the ledger is unavailable.

    Do NOT call this for worker-side gate-block / dispatch-exception captures —
    those are emitted by _mayor_worker; this ledger is Mayor-side only.
    """
    text = (
        f"Mayor ledger | action={action} bead={bead_id} "
        f"molecule={molecule} tier={tier or 'unknown'}"
    )
    if extra:
        text += f" | {extra}"
    _mayor_capture(runners, text, "decision")


# ---------------------------------------------------------------------------
# VB2 Refinery — batch-then-bisect + anti-starvation scoring
# ---------------------------------------------------------------------------

def _candidate_score(c: MergeCandidate, now: float) -> float:
    """Anti-starvation score for a merge candidate (pure).

    Higher score merges first.  Combines wait time (older first), retry count
    (already-bounced branches prioritized) and bead priority (lower priority
    number = higher precedence → larger contribution).  ``now`` is injected so
    the function is fully deterministic in tests (no time.monotonic inside).
    """
    age = max(0.0, now - c.verified_at)
    return (
        LOOP_REFINERY_W_AGE * age
        + LOOP_REFINERY_W_RETRY * c.attempts
        - LOOP_REFINERY_W_PRIO * c.priority
    )


def order_by_score(candidates: List[MergeCandidate], now: float) -> List[MergeCandidate]:
    """Return candidates ordered highest-score-first (anti-starvation).

    Pure: does not mutate the input.  Deterministic tie-break by bead_id so the
    orderer is stable regardless of insertion order.
    """
    return sorted(
        candidates,
        key=lambda c: (-_candidate_score(c, now), c.bead_id),
    )


def _conflict_outcome(c: MergeCandidate, cfg: RunConfig) -> RefineOutcome:
    """Classify a single-branch failure: re-implement (bounded) or escalate.

    A branch reaching this point either won't apply (textual conflict) or fails
    V on its own (semantic conflict) — both mean the working branch moved under
    the worker.  Bounded by cfg.refinery_attempts_max to stop conflict loops.
    """
    if c.attempts >= cfg.refinery_attempts_max:
        return RefineOutcome(candidate=c, kind="exhausted")
    return RefineOutcome(candidate=c, kind="reimplement")


def _try_batch(
    batch: List[MergeCandidate],
    runners: Runners,
    cfg: RunConfig,
) -> List[RefineOutcome]:
    """Merge+verify a batch atomically; bisect to isolate offenders on red.

    MUST be called with _MERGE_LOCK already held (refine holds the slot around
    the whole cycle; the lock is non-reentrant so we never re-acquire here).

    All-green: one verify lands the whole batch (K close for 1 verify).
    Red: roll back to the pre-batch snapshot, then
      - len == 1 → the culprit → conflict→re-implement (or escalate).
      - len  > 1 → split in half and recurse; a clean half merges and stays,
        only the failing partition is split further (innocent branches never
        penalized — they land in their green sub-batch).
    """
    if not batch:
        return []

    snapshot = runners.git_snapshot(cfg.branch) if runners.git_snapshot else ""
    merged_ok = runners.merge_batch(batch) if runners.merge_batch else False

    if merged_ok:
        verify_exit = runners.run_verify(cfg.verify_cmd, LOOP_VERIFY_TIMEOUT_S)
        if verify_exit == 0:
            return [RefineOutcome(candidate=c, kind="merged") for c in batch]

    # Batch is red (textual conflict or combined-V failure) — atomic rollback.
    if runners.git_reset is not None:
        runners.git_reset(snapshot)

    if len(batch) == 1:
        return [_conflict_outcome(batch[0], cfg)]

    mid = len(batch) // 2
    left = _try_batch(batch[:mid], runners, cfg)
    right = _try_batch(batch[mid:], runners, cfg)
    return left + right


def refine(
    merge_queue: List[MergeCandidate],
    runners: Runners,
    cfg: RunConfig,
    now: float,
) -> List[RefineOutcome]:
    """Drain up to cfg.batch_max candidates from *merge_queue* (anti-starvation).

    Selects the top-scored batch, removes it from the queue (mutates in place),
    and processes it under the merge slot (_MERGE_LOCK held around the whole
    batch-then-bisect cycle — the single-writer / merge-slot invariant).
    Returns one RefineOutcome per processed candidate; candidates beyond
    batch_max stay queued and are re-scored on the next call.

    Pure with respect to bead state — the caller applies close / relabel /
    return-to-ready based on the returned outcomes (Mayor single-writer).
    """
    if not merge_queue:
        return []

    batch_max = max(1, cfg.batch_max)
    ordered = order_by_score(merge_queue, now)
    batch = ordered[:batch_max]

    # Remove the selected batch from the queue (leftovers re-scored next call).
    selected_ids = {id(c) for c in batch}
    merge_queue[:] = [c for c in merge_queue if id(c) not in selected_ids]

    with _MERGE_LOCK:
        return _try_batch(batch, runners, cfg)


# ---------------------------------------------------------------------------
# Mayor loop — bounded-concurrent drain
# ---------------------------------------------------------------------------

def run_mayor_loop(cfg: RunConfig, runners: Runners) -> MayorSummary:
    """Execute beads as a bounded-concurrent Mayor.

    Dispatches up to cfg.max_workers ready beads in parallel. As workers finish,
    freed slots are filled with newly-unblocked beads. Only the Mayor (this
    function, main thread) mutates bead status — workers return WorkerResult
    structs and the Mayor reads and acts on them.

    Crash-aware: a crashed/timed-out worker moves its bead_id to recovery_blocked.
    Its bead stays in_progress and the slot remains occupied — capacity is never
    silently reclaimed.

    P2 reconciler: once per tick (after slot-filling, before/with the completion
    wait) the Mayor runs detect → guard-ladder → AI-judge and applies the resulting
    ReconcileActions.  Only the Mayor applies mutations — the reconcile function
    is pure-ish and returns actions for the Mayor to execute.
      - kill   → free slot, bead left failed/open (no close)
      - respawn → clear slot, bead returns to ready set for redispatch; capped by
                  cfg.max_respawns to stop respawn loops
      - wait   → leave as-is; re-evaluated next tick

    Backward compat: cfg.max_workers=1 produces sequential behavior compatible
    with the existing run_loop (same beads closed, same order).
    """
    summary = MayorSummary()
    active: Dict[str, WorkerHandle] = {}          # in-flight futures keyed by bead_id
    recovery_blocked: Set[str] = set()             # crashed; slot occupied; bead in_progress
    # P2: per-bead respawn counter (prevents respawn loops)
    respawn_counts: Dict[str, int] = {}
    # P2: Mayor-maintained bead status snapshot (injected into reconcile)
    bead_statuses: Dict[str, str] = {}
    # VB2 Refinery: V-passed branches awaiting integration (drained under the
    # merge slot each tick) + per-bead re-implement counter (anti-loop, persists
    # across re-dispatches so the refinery_attempts_max cap is enforced) + the
    # priority of each dispatched bead (carried onto MergeCandidates for scoring).
    merge_queue: List[MergeCandidate] = []
    refinery_attempts: Dict[str, int] = {}
    bead_priorities: Dict[str, int] = {}
    # VB2 active only when batch_max > 1 AND the batch seam is wired; otherwise
    # the V-pass path stays on the VA0b inline merge (full backward compat).
    refinery_on = cfg.batch_max > 1 and runners.merge_batch is not None

    # Abandonment registry: tracks (worktree_path, branch_name) for each bead
    # whose worktree was created but whose WorkerResult has not yet been processed
    # (i.e., worker is still in-flight).  Written from worker threads via a
    # thread-safe tracking wrapper around runners.worktree_create; read by the
    # Mayor's finally-block abandonment cleanup so it can teardown in-flight
    # worktrees without waiting for the workers to return.
    _worktree_registry: Dict[str, tuple] = {}
    _worktree_registry_lock = threading.Lock()

    # Build a tracking-wrapped worktree_create that records (path, branch) for
    # every worktree the workers create, keyed by bead_id.
    _original_worktree_create = runners.worktree_create
    if _original_worktree_create is not None:
        def _tracking_worktree_create(bead_id: str) -> Optional[tuple]:
            result = _original_worktree_create(bead_id)
            if result is not None:
                with _worktree_registry_lock:
                    _worktree_registry[bead_id] = result
            return result
        # Rebuild Runners using only declared dataclass fields so that any
        # extra attributes injected by tests (e.g. runners._merge_calls) are
        # not forwarded to the constructor — they would cause a TypeError.
        _runner_fields = {f.name for f in dataclasses.fields(runners)}
        runners = runners.__class__(**{
            **{k: v for k, v in runners.__dict__.items() if k in _runner_fields},
            "worktree_create": _tracking_worktree_create,
        })

    def _safe_teardown(
        worktree_path: Optional[str],
        branch_name: Optional[str],
        ctx: str,
        bead_id: Optional[str] = None,
    ) -> None:
        # Remove from the abandonment registry so a still-running bead_id that
        # completes normally does not get double-torn-down in the finally block.
        if bead_id is not None:
            with _worktree_registry_lock:
                _worktree_registry.pop(bead_id, None)
        if worktree_path is not None and runners.worktree_teardown is not None:
            try:
                runners.worktree_teardown(worktree_path, branch_name or "")
            except Exception as td_exc:
                logger.warning("Mayor: worktree teardown (%s) failed: %s", ctx, td_exc)

    def _safe_relabel(bead_id: str, label: str) -> None:
        if runners.beads_relabel is not None:
            try:
                runners.beads_relabel(bead_id, label)
            except Exception as rl_exc:
                logger.warning("Mayor: beads_relabel %s=%s failed: %s", bead_id, label, rl_exc)

    state_path: Path = (
        runners.loop_state_path if runners.loop_state_path is not None else LOOP_STATE_PATH
    )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_workers)

    # OBS2 — write START state before entering the tick loop
    write_loop_state(
        _build_mayor_state(
            summary, cfg,
            status="running",
            stop_reason=None,
            active_handles=active,
            recovery_blocked=recovery_blocked,
            now_monotonic=time.monotonic(),
            active=True,
        ),
        state_path,
    )

    try:
        while True:
            # ---- 1. Governor stop-check ----
            ok, reason = should_continue_mayor(summary, cfg, active, recovery_blocked)
            if not ok:
                summary.stop_reason = reason
                break

            # ---- 2. Fill free slots ----
            ready = runners.beads_ready(cfg.molecule)
            occupied = set(active.keys()) | recovery_blocked
            free = cfg.max_workers - len(active) - len(recovery_blocked)
            to_dispatch = _pick(ready, free, occupied)

            if cfg.dry_run:
                # DRY-RUN: print the plan for each bead we WOULD dispatch — no mutations.
                if to_dispatch:
                    for bead in to_dispatch:
                        bead_id = bead["id"]
                        tier = route_model(bead)
                        verify_cmd = resolve_verify_cmd(bead, cfg.verify_cmd)
                        try:
                            prompt = compose_dispatch(
                                bead,
                                cfg.repo,
                                cfg.branch,
                                verify_cmd or cfg.verify_cmd,
                                recall_context="",
                            )
                            _print_dry_run_plan(bead, tier, verify_cmd, prompt)
                        except ValueError as exc:
                            print(
                                f"\n[dry-run] Bead {bead_id} would be GATE-BLOCKED: {exc}"
                            )
                    summary.stop_reason = "once" if cfg.once else "queue-empty"
                    break
                else:
                    summary.stop_reason = "queue-empty"
                    break

            for bead in to_dispatch:
                bead_id = bead["id"]
                # Mayor marks in_progress BEFORE submitting to pool (single-writer)
                if runners.beads_update is not None:
                    runners.beads_update(bead_id, "in_progress")
                bead_statuses[bead_id] = "in_progress"
                # VB2: remember priority for anti-starvation scoring of this
                # bead's eventual MergeCandidate (only the bead_id survives to
                # the completion handler).
                bead_priorities[bead_id] = bead.get("priority", 99)
                future = pool.submit(_mayor_worker, bead, cfg, runners)
                handle = WorkerHandle(
                    bead_id=bead_id,
                    future=future,
                    model=route_model(bead),
                    started_at=time.monotonic(),
                )
                active[bead_id] = handle
                # P3.2 — ledger: worker dispatched
                _ledger_capture(
                    runners,
                    action="dispatch",
                    bead_id=bead_id,
                    molecule=cfg.molecule,
                    tier=handle.model,
                )

            # ---- 2b. P2 reconcile — once per tick, Mayor applies actions ----
            # reconcile is pure: detect → guard-ladder → AI-judge → returns actions.
            # The Mayor (this thread) is the sole executor of mutations.
            recon_actions = _reconcile(
                active=active,
                recovery_blocked=recovery_blocked,
                bead_statuses=bead_statuses,
                respawn_counts=respawn_counts,
                cfg_stuck_threshold_s=cfg.stuck_threshold_s,
                cfg_spawning_window_s=cfg.spawning_window_s,
                cfg_max_respawns=cfg.max_respawns,
                now=time.monotonic(),
                judge=runners.judge,
            )
            for action in recon_actions:
                bid = action.bead_id
                decision = action.decision
                if decision == "kill":
                    # Mayor frees the slot; bead left in_progress/failed (not closed)
                    logger.info("reconcile: killing bead %s", bid)
                    active.pop(bid, None)
                    recovery_blocked.discard(bid)
                    bead_statuses[bid] = "failed"
                    summary.failed += 1
                    _mayor_capture(
                        runners,
                        f"Mayor reconcile: bead {bid} killed by judge.", "pattern"
                    )
                elif decision == "respawn":
                    # Mayor clears the slot; bead returns to ready set for redispatch
                    logger.info("reconcile: respawning bead %s", bid)
                    active.pop(bid, None)
                    recovery_blocked.discard(bid)
                    bead_statuses.pop(bid, None)
                    respawn_counts[bid] = respawn_counts.get(bid, 0) + 1
                    # Reopen the bead for redispatch (Mayor single-writer)
                    if runners.beads_update is not None:
                        runners.beads_update(bid, "open")
                    _mayor_capture(
                        runners,
                        f"Mayor reconcile: bead {bid} respawned "
                        f"(count={respawn_counts[bid]}).",
                        "pattern",
                    )
                else:
                    # "wait" — leave as-is; re-evaluated next tick
                    logger.debug("reconcile: waiting on bead %s", bid)

            # OBS2 P3.1 — write per-tick state after slot-filling + reconcile
            write_loop_state(
                _build_mayor_state(
                    summary, cfg,
                    status="running",
                    stop_reason=None,
                    active_handles=active,
                    recovery_blocked=recovery_blocked,
                    now_monotonic=time.monotonic(),
                    active=True,
                ),
                state_path,
            )

            # ---- 3. Check stop: nothing running AND nothing ready AND nothing recovering ----
            if not active:
                # No work in flight. Check if recovery_blocked fills capacity.
                # (should_continue_mayor already catches the capacity-exhausted case above)
                ready_check = runners.beads_ready(cfg.molecule)
                if not ready_check and not recovery_blocked:
                    summary.stop_reason = "queue-empty"
                    break
                if not ready_check and recovery_blocked:
                    # All remaining capacity is stuck — should_continue_mayor handles this
                    # on the next iteration, but we need to trigger the check now.
                    ok2, reason2 = should_continue_mayor(summary, cfg, active, recovery_blocked)
                    if not ok2:
                        summary.stop_reason = reason2
                        break
                    # If we get here, the stop condition wasn't met — shouldn't happen
                    # in well-formed usage, but break to avoid an infinite spin.
                    summary.stop_reason = "capacity-exhausted-by-stuck-workers"
                    break

            if not active:
                # Still nothing after the above checks — queue-empty
                summary.stop_reason = "queue-empty"
                break

            # ---- 4. Wait for ANY worker to complete ----
            done_futures, _ = concurrent.futures.wait(
                [h.future for h in active.values()],
                return_when=concurrent.futures.FIRST_COMPLETED,
                timeout=LOOP_ITER_TIMEOUT_S,
            )

            # ---- 5. Process completed futures (Mayor writes) ----
            completed_bead_ids = [
                bid for bid, h in active.items() if h.future in done_futures
            ]

            any_closed = False
            for bead_id in completed_bead_ids:
                handle = active.pop(bead_id)
                try:
                    res: WorkerResult = handle.future.result(timeout=0)
                except Exception as exc:
                    # Future raised an unexpected exception
                    logger.warning("Worker future for %s raised: %s", bead_id, exc)
                    recovery_blocked.add(bead_id)
                    bead_statuses[bead_id] = "in_progress"  # stays in_progress until reconciled
                    summary.failed += 1
                    _mayor_capture(
                        runners,
                        f"Mayor: worker future for {bead_id} raised unexpectedly: {exc}",
                        "pattern",
                    )
                    continue

                # Accumulate tokens inline — res is available here; no second future.result call needed.
                summary.total_tokens += res.dispatch_result.get("tokens", 0)

                if res.rate_limited:
                    # VA1: RATE_LIMITED ≠ FAILED.  Never burn the bead — return it
                    # to the ready set (open) so a clean resume re-dispatches it
                    # once the rate-limit window clears.  Discard any partial
                    # worktree code and flag the governor to pause-stop the run.
                    bead_statuses.pop(bead_id, None)
                    summary.rate_limited = True
                    summary.rate_limited_beads += 1
                    if runners.beads_update is not None:
                        runners.beads_update(bead_id, "open")
                    _mayor_capture(
                        runners,
                        f"Mayor: bead {bead_id} RATE_LIMITED — returned to ready set, "
                        f"pausing run for clean resume (not a failure).",
                        "pattern",
                    )
                    _ledger_capture(
                        runners,
                        action="rate-limited",
                        bead_id=bead_id,
                        molecule=cfg.molecule,
                        tier=handle.model,
                    )
                    # Discard partial worktree code (VA0b lifecycle).
                    _safe_teardown(res.worktree_path, res.branch_name, "rate-limited", bead_id=bead_id)
                elif res.timed_out or res.error is not None:
                    # Worker crashed or timed out — slot stays occupied
                    recovery_blocked.add(bead_id)
                    bead_statuses[bead_id] = "in_progress"  # stays in_progress until reconciled
                    summary.failed += 1
                    _mayor_capture(
                        runners,
                        f"Mayor: bead {bead_id} moved to recovery_blocked. "
                        f"error={res.error} timed_out={res.timed_out}",
                        "pattern",
                    )
                    # P3.2 — ledger: verify failed (crash/timeout counts as verify-fail)
                    _ledger_capture(
                        runners,
                        action="verify-fail",
                        bead_id=bead_id,
                        molecule=cfg.molecule,
                        tier=handle.model,
                        extra=f"timed_out={res.timed_out} error={res.error}",
                    )
                    # VA0b: tear down worktree even on crash (discard the code)
                    _safe_teardown(res.worktree_path, res.branch_name, "crash", bead_id=bead_id)
                elif res.verify_exit == 0:
                    # V passed.
                    if refinery_on and res.branch_name is not None:
                        # VB2 Refinery: enqueue a MergeCandidate; the merge slot
                        # drains the queue (batch-then-bisect) after this loop.
                        # The Mayor never merges here — single-writer preserved.
                        merge_queue.append(MergeCandidate(
                            bead_id=bead_id,
                            branch_name=res.branch_name,
                            worktree_path=res.worktree_path,
                            model=handle.model,
                            verified_at=time.monotonic(),
                            priority=bead_priorities.get(bead_id, 99),
                            attempts=refinery_attempts.get(bead_id, 0),
                        ))
                        _ledger_capture(
                            runners,
                            action="verify-pass",
                            bead_id=bead_id,
                            molecule=cfg.molecule,
                            tier=handle.model,
                            extra=f"verify_exit=0 branch={res.branch_name} queued-for-refinery",
                        )
                    # VA0b: merge the named branch (serialized), then close
                    elif res.branch_name is not None and runners.merge_branch is not None:
                        with _MERGE_LOCK:
                            merge_exit = runners.merge_branch(res.branch_name)
                        if merge_exit != 0:
                            # Merge conflict — treat as failure; leave bead open
                            bead_statuses.pop(bead_id, None)
                            summary.failed += 1
                            _mayor_capture(
                                runners,
                                f"Mayor: bead {bead_id} merge FAILED (exit={merge_exit}) "
                                f"for branch {res.branch_name}. Bead left open.",
                                "pattern",
                            )
                            _ledger_capture(
                                runners,
                                action="merge-fail",
                                bead_id=bead_id,
                                molecule=cfg.molecule,
                                tier=handle.model,
                                extra=f"branch={res.branch_name} merge_exit={merge_exit}",
                            )
                            # Teardown without close
                            _safe_teardown(res.worktree_path, res.branch_name, "merge-fail", bead_id=bead_id)
                            # Skip close — continue to next bead
                        else:
                            # Merge succeeded — now close and teardown
                            runners.beads_close(bead_id)
                            bead_statuses[bead_id] = "closed"
                            summary.closed += 1
                            any_closed = True
                            _mayor_capture(
                                runners,
                                f"Mayor: bead {bead_id} closed after V=0 + merge {res.branch_name}.",
                                "decision",
                            )
                            _ledger_capture(
                                runners,
                                action="verify-pass",
                                bead_id=bead_id,
                                molecule=cfg.molecule,
                                tier=handle.model,
                                extra=f"verify_exit=0 branch={res.branch_name}",
                            )
                            _ledger_capture(
                                runners,
                                action="close",
                                bead_id=bead_id,
                                molecule=cfg.molecule,
                                tier=handle.model,
                            )
                            # Teardown AFTER close (Mayor is single-writer)
                            _safe_teardown(res.worktree_path, res.branch_name, "post-close", bead_id=bead_id)
                    else:
                        # No VA0b branch — plain V-passed path (backward compat)
                        runners.beads_close(bead_id)
                        bead_statuses[bead_id] = "closed"
                        summary.closed += 1
                        any_closed = True
                        _mayor_capture(
                            runners,
                            f"Mayor: bead {bead_id} closed after verify_exit=0.",
                            "decision",
                        )
                        # P3.2 — ledger: verify passed then close
                        _ledger_capture(
                            runners,
                            action="verify-pass",
                            bead_id=bead_id,
                            molecule=cfg.molecule,
                            tier=handle.model,
                            extra="verify_exit=0",
                        )
                        _ledger_capture(
                            runners,
                            action="close",
                            bead_id=bead_id,
                            molecule=cfg.molecule,
                            tier=handle.model,
                        )
                else:
                    # V failed or no verify cmd — leave open for reconciler/next round
                    bead_statuses.pop(bead_id, None)
                    summary.failed += 1
                    _mayor_capture(
                        runners,
                        f"Mayor: bead {bead_id} NOT closed — verify_exit={res.verify_exit}.",
                        "pattern",
                    )
                    # P3.2 — ledger: verify failed
                    _ledger_capture(
                        runners,
                        action="verify-fail",
                        bead_id=bead_id,
                        molecule=cfg.molecule,
                        tier=handle.model,
                        extra=f"verify_exit={res.verify_exit}",
                    )
                    # VA0b: tear down worktree on V-fail (discard code, bead stays open)
                    _safe_teardown(res.worktree_path, res.branch_name, "V-fail", bead_id=bead_id)

            # ---- 5b. VB2 Refinery — drain the merge queue under the merge slot ----
            # Fully drain each tick so no V-passed branch is ever left unmerged
            # when the loop stops.  refine() holds _MERGE_LOCK around each batch
            # cycle (single-writer / merge-slot); the Mayor applies outcomes here.
            while merge_queue:
                outcomes = refine(merge_queue, runners, cfg, time.monotonic())
                if not outcomes:
                    break
                for outcome in outcomes:
                    c = outcome.candidate
                    if outcome.kind == "merged":
                        runners.beads_close(c.bead_id)
                        bead_statuses[c.bead_id] = "closed"
                        summary.closed += 1
                        any_closed = True
                        refinery_attempts.pop(c.bead_id, None)
                        _ledger_capture(
                            runners, action="batch-merge", bead_id=c.bead_id,
                            molecule=cfg.molecule, tier=c.model,
                            extra=f"branch={c.branch_name}",
                        )
                        _ledger_capture(
                            runners, action="close", bead_id=c.bead_id,
                            molecule=cfg.molecule, tier=c.model,
                        )
                        _safe_teardown(c.worktree_path, c.branch_name, "refinery-merged", bead_id=c.bead_id)
                    elif outcome.kind == "reimplement":
                        # Typed conflict → relabel + return to ready (re-implement
                        # against the now-advanced HEAD), bounded by the attempts cap.
                        refinery_attempts[c.bead_id] = c.attempts + 1
                        _safe_relabel(c.bead_id, "conflict:re-implement")
                        _safe_relabel(
                            c.bead_id,
                            f"refinery-attempts:{refinery_attempts[c.bead_id]}",
                        )
                        if runners.beads_update is not None:
                            runners.beads_update(c.bead_id, "open")
                        bead_statuses.pop(c.bead_id, None)
                        _mayor_capture(
                            runners,
                            f"Mayor Refinery: bead {c.bead_id} conflict → re-implement "
                            f"(attempt {refinery_attempts[c.bead_id]}/{cfg.refinery_attempts_max}).",
                            "pattern",
                        )
                        _ledger_capture(
                            runners, action="conflict-reimplement", bead_id=c.bead_id,
                            molecule=cfg.molecule, tier=c.model,
                            extra=f"branch={c.branch_name} attempt={refinery_attempts[c.bead_id]}",
                        )
                        _safe_teardown(c.worktree_path, c.branch_name, "refinery-reimplement", bead_id=c.bead_id)
                    else:  # "exhausted" — escalate (do NOT respawn; surface to operator)
                        summary.failed += 1
                        _mayor_capture(
                            runners,
                            f"Mayor Refinery: bead {c.bead_id} EXHAUSTED re-implement cap "
                            f"({cfg.refinery_attempts_max}) — escalating, left for operator.",
                            "pattern",
                        )
                        _ledger_capture(
                            runners, action="refinery-exhausted", bead_id=c.bead_id,
                            molecule=cfg.molecule, tier=c.model,
                            extra=f"branch={c.branch_name} attempts={c.attempts}",
                        )
                        _safe_teardown(c.worktree_path, c.branch_name, "refinery-exhausted", bead_id=c.bead_id)

            summary.iterations += 1
            if not any_closed:
                summary.consecutive_zero_close += 1
            else:
                summary.consecutive_zero_close = 0

            # --once: stop after the first completion round (mirrors run_loop semantics)
            if cfg.once:
                summary.stop_reason = "once"
                break

    finally:
        # Abandonment cleanup: for every bead still in-flight (active dict) or
        # recovery_blocked, reset the bead to "open" so it resumes cleanly on the
        # next run (never left stuck in_progress), then teardown its worktree.
        # The Mayor is the single-writer — do this here, before pool.shutdown,
        # so the status writes happen before orphaned claude -p threads finish.
        for abandon_bead_id in list(active.keys()):
            handle = active.get(abandon_bead_id)
            if handle is not None:
                # Cancel the future if not already done (best-effort; thread may be running)
                handle.future.cancel()
            if runners.beads_update is not None:
                try:
                    runners.beads_update(abandon_bead_id, "open")
                except Exception as ab_exc:
                    logger.warning(
                        "Mayor: abandonment reset for %s failed: %s", abandon_bead_id, ab_exc
                    )
            logger.info("Mayor: abandoned in-flight bead %s reset to open", abandon_bead_id)
            _mayor_capture(
                runners,
                f"Mayor: bead {abandon_bead_id} abandoned on loop exit — reset to open.",
                "pattern",
            )
            # Teardown the worktree for this in-flight worker if one was created.
            # The worktree_path/branch are read from the tracking registry (populated
            # by _tracking_worktree_create from the worker thread at creation time).
            with _worktree_registry_lock:
                wt_info = _worktree_registry.pop(abandon_bead_id, None)
            if wt_info is not None:
                _safe_teardown(wt_info[0], wt_info[1], "abandonment")

        for abandon_bead_id in sorted(recovery_blocked):
            if runners.beads_update is not None:
                try:
                    runners.beads_update(abandon_bead_id, "open")
                except Exception as rb_exc:
                    logger.warning(
                        "Mayor: recovery_blocked reset for %s failed: %s", abandon_bead_id, rb_exc
                    )
            logger.info(
                "Mayor: recovery_blocked bead %s reset to open on loop exit", abandon_bead_id
            )
            _mayor_capture(
                runners,
                f"Mayor: recovery_blocked bead {abandon_bead_id} reset to open on loop exit.",
                "pattern",
            )
            # Teardown any worktree that was registered for this bead
            with _worktree_registry_lock:
                wt_info = _worktree_registry.pop(abandon_bead_id, None)
            if wt_info is not None:
                _safe_teardown(wt_info[0], wt_info[1], "abandonment-recovery-blocked")

        # Drain any un-merged VB2 refinery queue entries: beads that passed V
        # but whose branch was not yet merged before the loop broke.  Reset them
        # to "open" so they re-run on the next session, and teardown worktrees.
        for mc in merge_queue:
            if runners.beads_update is not None:
                try:
                    runners.beads_update(mc.bead_id, "open")
                except Exception as mq_exc:
                    logger.warning(
                        "Mayor: merge_queue abandonment reset for %s failed: %s",
                        mc.bead_id, mq_exc,
                    )
            _safe_teardown(mc.worktree_path, mc.branch_name, "abandonment-merge-queue", bead_id=mc.bead_id)
        merge_queue.clear()

        pool.shutdown(wait=False)

    if not summary.stop_reason:
        summary.stop_reason = "unknown"

    # OBS2 P3.1 — write TERMINAL state after the Mayor loop exits
    terminal_status = "done" if summary.stop_reason in (
        "queue-empty", "once", "max-iterations", "budget-exhausted",
        "no-progress", "capacity-exhausted-by-stuck-workers",
    ) else "stopped"
    write_loop_state(
        _build_mayor_state(
            summary, cfg,
            status=terminal_status,
            stop_reason=summary.stop_reason,
            active_handles=active,
            recovery_blocked=recovery_blocked,
            now_monotonic=time.monotonic(),
            active=False,
        ),
        state_path,
    )

    return summary


def should_continue_mayor(
    summary: MayorSummary,
    cfg: RunConfig,
    active: Dict[str, "WorkerHandle"],
    recovery_blocked: Set[str],
) -> tuple[bool, str]:
    """Governor stop-check for run_mayor_loop.

    Stop conditions (checked in priority order):
      - iterations >= max_iterations                                    → "max-iterations"
      - total_tokens >= budget_tokens                                   → "budget-exhausted"
      - capacity-exhausted: recovery_blocked >= max_workers AND active empty → "capacity-exhausted-by-stuck-workers"
        (checked before no-progress: when capacity is stuck, naming it precisely is more useful)
      - no-progress: consecutive_zero_close >= K AND active is empty    → "no-progress"
    """
    if summary.iterations >= cfg.max_iterations:
        return False, "max-iterations"
    if summary.total_tokens >= cfg.budget_tokens:
        return False, "budget-exhausted"
    # VA1: rate-limit pause-stop — a graceful, resumable stop.  Checked before
    # the capacity/no-progress signals: the rate-limited bead has been returned
    # to the ready set (never burned), so stopping here lets the run resume
    # cleanly once the provider rate-limit window clears rather than spinning.
    if summary.rate_limited:
        return False, "rate-limited"
    # capacity-exhausted before no-progress: when workers fill all slots with crashes,
    # the specific reason is more diagnostic than the generic no-progress signal.
    if len(recovery_blocked) >= cfg.max_workers and not active:
        return False, "capacity-exhausted-by-stuck-workers"
    if summary.consecutive_zero_close >= LOOP_NOPROGRESS_K and not active:
        return False, "no-progress"
    return True, ""


# ---------------------------------------------------------------------------
# Run loop — accumulates IterationResults, applies governor
# ---------------------------------------------------------------------------

def run_loop(cfg: RunConfig, runners: Runners) -> RunSummary:
    """Execute the full loop until a stop condition fires."""
    summary = RunSummary(stop_reason="")

    # Resolve the state file path: runner override wins, else module default.
    state_path: Path = runners.loop_state_path if runners.loop_state_path is not None else LOOP_STATE_PATH

    # Determine initial status (dry-run writes "dry-run" to allow observation).
    running_status = "dry-run" if cfg.dry_run else "running"

    # OBS2 — write START state
    write_loop_state(
        _build_state(
            summary,
            cfg,
            status=running_status,
            last_bead=None,
            last_outcome=None,
            stop_reason=None,
            active=True,
        ),
        state_path,
    )

    last_bead: Optional[str] = None
    last_outcome: Optional[str] = None

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

        if result.bead_id is not None:
            last_bead = result.bead_id
        last_outcome = result.outcome

        if result.outcome == "closed":
            summary.beads_closed += 1
            summary.consecutive_zero_close = 0
        elif result.outcome == "empty":
            summary.stop_reason = "queue-empty"
            # OBS2 — write per-iteration state before breaking
            write_loop_state(
                _build_state(
                    summary,
                    cfg,
                    status=running_status,
                    last_bead=last_bead,
                    last_outcome=last_outcome,
                    stop_reason=None,
                    active=True,
                ),
                state_path,
            )
            break
        else:
            summary.consecutive_zero_close += 1

        # OBS2 — write per-iteration state after accumulating counts
        write_loop_state(
            _build_state(
                summary,
                cfg,
                status=running_status,
                last_bead=last_bead,
                last_outcome=last_outcome,
                stop_reason=None,
                active=True,
            ),
            state_path,
        )

        # --once: exactly one real iteration then exit
        if cfg.once:
            summary.stop_reason = "once"
            break

    if not summary.stop_reason:
        summary.stop_reason = "unknown"

    # OBS2 — write TERMINAL state
    # Dry-run keeps "dry-run" as the status throughout (including termination).
    # Live runs use "done" for normal exits, "stopped" for unexpected ones.
    if cfg.dry_run:
        terminal_status = "dry-run"
    elif summary.stop_reason in (
        "queue-empty", "once", "max-iterations", "budget-exhausted", "no-progress"
    ):
        terminal_status = "done"
    else:
        terminal_status = "stopped"
    write_loop_state(
        _build_state(
            summary,
            cfg,
            status=terminal_status,
            last_bead=last_bead,
            last_outcome=last_outcome,
            stop_reason=summary.stop_reason,
            active=False,
        ),
        state_path,
    )

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

def _default_repo() -> str:
    """Default --repo: the cwd the runner is invoked from.

    The runner is always launched from inside the target repo
    (``cd <repo> && python3 ... loop_runner.py``), so the working directory is
    the correct, machine-independent default. Replaces the former hardcoded
    developer path. Mirrors the Pi engine (``cfg.repo ?? process.cwd()``).
    """
    return os.getcwd()


def _default_branch() -> str:
    """Default --branch: ``main``.

    The working/integration branch the Mayor merges passing worktree branches
    into. Replaces the former hardcoded WIP-branch default. Mirrors the Pi
    engine (``cfg.branch ?? "main"``).
    """
    return "main"


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
        default=_default_repo(),
        help=(
            "Repo path passed into the dispatch prompt and used as the worktree "
            "source (default: the current working directory)."
        ),
    )
    parser.add_argument(
        "--branch",
        default=_default_branch(),
        help=(
            "Working/integration branch the Mayor merges passing worktree "
            "branches into (default: main)."
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help=(
            "Maximum concurrent worker beads (default: 1 = sequential, "
            "matches today's behavior). Values > 1 activate the Mayor concurrent loop."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    # P2 reconciler config
    parser.add_argument(
        "--stuck-threshold",
        type=float,
        default=LOOP_STUCK_THRESHOLD_S,
        help=(
            f"Seconds before a hung worker is considered stuck (default: {LOOP_STUCK_THRESHOLD_S})."
        ),
    )
    parser.add_argument(
        "--spawning-window",
        type=float,
        default=LOOP_SPAWNING_WINDOW_S,
        help=(
            f"Seconds of grace period before a new worker can be stuck-detected "
            f"(default: {LOOP_SPAWNING_WINDOW_S})."
        ),
    )
    parser.add_argument(
        "--max-respawns",
        type=int,
        default=LOOP_MAX_RESPAWNS,
        help=(
            f"Maximum times a bead may be respawned by the reconciler "
            f"(default: {LOOP_MAX_RESPAWNS}). Set to 0 to disable respawning."
        ),
    )
    # VB2 Refinery config
    parser.add_argument(
        "--batch-max",
        type=int,
        default=1,
        help=(
            "VB2 Refinery: max V-passed branches merged+verified per batch "
            "(default: 1 = VA0b serial merge). Values > 1 activate batch-then-bisect."
        ),
    )
    parser.add_argument(
        "--refinery-attempts",
        type=int,
        default=LOOP_REFINERY_ATTEMPTS_MAX,
        help=(
            f"VB2 Refinery: max conflict→re-implement attempts before a bead is "
            f"escalated (default: {LOOP_REFINERY_ATTEMPTS_MAX})."
        ),
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
        max_workers=args.max_workers,
        stuck_threshold_s=args.stuck_threshold,
        spawning_window_s=args.spawning_window,
        max_respawns=args.max_respawns,
        batch_max=args.batch_max,
        refinery_attempts_max=args.refinery_attempts,
    )

    if cfg.dry_run:
        print(f"[dry-run] OptivAI Loop runner — molecule={cfg.molecule!r}")
        print(f"[dry-run] max_workers={cfg.max_workers}  "
              f"max_iterations={cfg.max_iterations}  budget_tokens={cfg.budget_tokens:,}")
        print(f"[dry-run] verify_cmd={cfg.verify_cmd!r}")
        print(f"[dry-run] No mutations will be performed.\n")

    runners = make_live_runners()

    # Route to the Mayor concurrent loop when max_workers > 1; otherwise the
    # existing sequential run_loop (max_workers=1 is the backward-compatible default).
    if cfg.max_workers > 1:
        mayor_summary = run_mayor_loop(cfg, runners)
        print(f"\n[mayor] Run complete — stop_reason={mayor_summary.stop_reason!r}")
        print(f"[mayor] iterations={mayor_summary.iterations}  "
              f"beads_closed={mayor_summary.closed}  "
              f"total_tokens={mayor_summary.total_tokens:,}")
        _clean_exits = (
            "queue-empty", "once", "max-iterations",
            "budget-exhausted", "no-progress",
            "capacity-exhausted-by-stuck-workers",
            # VA1: a rate-limit pause is an expected, resumable exit — exit 0 so
            # the scheduling layer re-invokes cleanly rather than flagging a crash.
            "rate-limited",
        )
        return 0 if mayor_summary.stop_reason in _clean_exits else 1

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
