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

# Default path for the shared loop state file (OBS2).
# Override via Runners.loop_state_path for testing.
LOOP_STATE_PATH: Path = Path.home() / ".claude" / "loop-state.json"

# Default verify command for this repo when none is specified
_REPO_DEFAULT_VERIFY_CMD = "cd /Users/erato949/dev/optivai-claude-plugin/scripts && python3 -m pytest -q"

# ---------------------------------------------------------------------------
# Worktree serialization lock (P1.2)
# Concurrent `git worktree add` calls race on the shared .git/config index.
# Workers may run dispatch in their own isolated worktrees, but worktree
# creation and teardown are serialized through this process-level lock.
# ---------------------------------------------------------------------------
_WORKTREE_LOCK: threading.Lock = threading.Lock()

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
    """

    bead_id: str
    dispatch_result: dict          # {"tokens": int, "output": str}
    verify_exit: Optional[int]     # exit code of V, or None on error
    error: Optional[Exception]     # set if the worker raised
    timed_out: bool = False        # set if the future timed out before completion


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
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            cwd=cwd,
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


@contextlib.contextmanager
def _live_worktree_manager(bead_id: str) -> Iterator[Optional[str]]:
    """Create an isolated git worktree for *bead_id*, yield its path, then remove it.

    Serialized through _WORKTREE_LOCK so concurrent workers never race on
    `git worktree add` (which modifies the shared .git/config and packed-refs).
    The lock is held only during creation and teardown; the dispatched subagent
    runs inside the worktree without holding the lock, so workers run truly in
    parallel while isolation is maintained.

    Yields None if:
    - not inside a git repository (git rev-parse fails)
    - git worktree add fails for any reason

    On either normal or exceptional exit, the worktree is removed (serialized).
    """
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


def make_live_runners() -> Runners:
    """Construct the real (live) Runners instance."""
    return Runners(
        beads_ready=_live_beads_ready,
        beads_close=_live_beads_close,
        beads_update=_live_beads_update,
        brain_recall=_live_brain_recall,
        brain_capture=_live_brain_capture,
        dispatch=_live_dispatch,
        run_verify=_live_run_verify,
        worktree_manager=_live_worktree_manager,
        dispatch_with_cwd=_live_dispatch_with_cwd,
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
    """Build the §1 schema dict for the loop state file.

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

    # EXECUTE — dispatch the subagent inside an isolated worktree when available.
    # If runners.worktree_manager is None, fall back to the plain dispatch path
    # (backward-compatible for tests and sequential runners that don't need isolation).
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

    state_path: Path = (
        runners.loop_state_path if runners.loop_state_path is not None else LOOP_STATE_PATH
    )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_workers)

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

            for bead in to_dispatch:
                bead_id = bead["id"]
                # Mayor marks in_progress BEFORE submitting to pool (single-writer)
                if runners.beads_update is not None:
                    runners.beads_update(bead_id, "in_progress")
                bead_statuses[bead_id] = "in_progress"
                future = pool.submit(_mayor_worker, bead, cfg, runners)
                handle = WorkerHandle(
                    bead_id=bead_id,
                    future=future,
                    model=route_model(bead),
                    started_at=time.monotonic(),
                )
                active[bead_id] = handle

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
                    runners.brain_capture(
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
                    runners.brain_capture(
                        f"Mayor reconcile: bead {bid} respawned "
                        f"(count={respawn_counts[bid]}).",
                        "pattern",
                    )
                else:
                    # "wait" — leave as-is; re-evaluated next tick
                    logger.debug("reconcile: waiting on bead %s", bid)

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
                    runners.brain_capture(
                        f"Mayor: worker future for {bead_id} raised unexpectedly: {exc}",
                        "pattern",
                    )
                    continue

                if res.timed_out or res.error is not None:
                    # Worker crashed or timed out — slot stays occupied
                    recovery_blocked.add(bead_id)
                    bead_statuses[bead_id] = "in_progress"  # stays in_progress until reconciled
                    summary.failed += 1
                    runners.brain_capture(
                        f"Mayor: bead {bead_id} moved to recovery_blocked. "
                        f"error={res.error} timed_out={res.timed_out}",
                        "pattern",
                    )
                elif res.verify_exit == 0:
                    # V passed — Mayor closes the bead (single-writer)
                    runners.beads_close(bead_id)
                    bead_statuses[bead_id] = "closed"
                    summary.closed += 1
                    any_closed = True
                    runners.brain_capture(
                        f"Mayor: bead {bead_id} closed after verify_exit=0.",
                        "decision",
                    )
                else:
                    # V failed or no verify cmd — leave open for reconciler/next round
                    bead_statuses.pop(bead_id, None)
                    summary.failed += 1
                    runners.brain_capture(
                        f"Mayor: bead {bead_id} NOT closed — verify_exit={res.verify_exit}.",
                        "pattern",
                    )

            summary.iterations += 1
            if not any_closed:
                summary.consecutive_zero_close += 1
            else:
                summary.consecutive_zero_close = 0

            # Accumulate tokens from done workers
            for bead_id in completed_bead_ids:
                # We already popped from active; find result via done_futures
                pass  # token accounting via WorkerResult was already done above
            for fut in done_futures:
                try:
                    r: WorkerResult = fut.result(timeout=0)
                    summary.total_tokens += r.dispatch_result.get("tokens", 0)
                except Exception:
                    pass

    finally:
        pool.shutdown(wait=False)

    if not summary.stop_reason:
        summary.stop_reason = "unknown"

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
