#!/usr/bin/env python3
"""Auto-recall hook — closes the capture/recall asymmetry in the dev harness.

This is the KEYSTONE recall hook (bead gz-h7r54, epic gz-r6ivl). It is the
symmetric counterpart to ``brain_hook.py``: that file auto-captures, this
file auto-recalls. The harness today auto-captures via ``brain_hook.py``
but never auto-recalls — ``context_primer.py`` primes only from local logs,
and recall is manual via the ``brain-*`` commands. Result: capture-heavy
recall-light agents that re-derive and act on stale assumptions. This hook
makes recall reflexive.

Invocation: from ``UserPromptSubmit`` in ``.claude/settings.json``, alongside
the existing ``user-prompt-submit.py`` (this hook is ADDED, not replacing).
The Claude Code hook protocol sends a JSON event on stdin with the user's
prompt; if the prompt is substantive, this hook runs a brain search and
emits ``additionalContext`` JSON to stdout. Claude Code prepends that
context to the agent's reasoning frame.

Fail-open contract (mandatory; matches ``brain_hook.py``): every error
class returns exit 0 with NO stdout. ``TimeoutExpired``,
``CalledProcessError``, ``JSONDecodeError``, ``FileNotFoundError``, bare
``Exception`` — all silent. The hook MUST NEVER cause a user prompt to fail
to submit.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# ─── Configuration ────────────────────────────────────────────────────────────

# Trigger keywords mark a short prompt as "substantive enough to recall against".
# Lowercased; case-insensitive matching is done on the full prompt at trigger
# time. Long prompts (>= 200 chars) bypass the keyword check.
TRIGGER_KEYWORDS = {
    "plan", "review", "decide", "design", "audit", "refactor", "debug",
    "analyze", "investigate", "scope", "ship", "spec", "diagnose",
    "compare", "evaluate", "fix", "implement",
}

MIN_PROMPT_LENGTH = 50          # below this, skip
LONG_PROMPT_BYPASS = 200        # at/above this, fire even without a keyword
QUERY_WINDOW_CHARS = 500        # send first N chars of prompt as the search query
RECALL_LIMIT = 5                # top-N atoms
SEARCH_TIMEOUT_SECONDS = 8      # subprocess timeout on the brain search
SUMMARY_MAX_CHARS = 200         # trim atom SUMMARY to N chars in output
SHORT_ID_CHARS = 8              # last N chars of THOUGHT_ID for the short_id
DATE_CHARS = 10                 # first N chars of CREATED_AT (YYYY-MM-DD)

# ─── Stale-state guard (enhancement #2 — bead gz-ow0sp) ───────────────────────
# After the recall pass, scan the prompt for bead IDs (gz-XXXXX) and brain atom
# IDs (brain-N-hex). Closed/done beads and superseded/forgotten atoms surface a
# "## Stale-state guard" section so the agent does not act on resolved work as
# if it were open. Suppressed entirely when no stale references are found.
BEAD_ID_PATTERN = re.compile(r"\bgz-[a-z0-9]{4,8}\b")
ATOM_ID_PATTERN = re.compile(r"\bbrain-\d+-[a-f0-9]+\b")
BEAD_ID_CAP = 10                 # max bead IDs per prompt to look up
ATOM_ID_CAP = 5                  # max atom IDs per prompt to look up
BEADS_SHOW_TIMEOUT_SECONDS = 8   # per-call timeout for `beads show`
ATOM_INSPECT_TIMEOUT_SECONDS = 8  # per-call timeout for open_brain --inspect
STALE_GUARD_BUDGET_SECONDS = 15  # total wall-clock budget for ALL bead lookups
STALE_BEAD_STATES = {"closed", "done"}  # which states warrant a warning
BEAD_TITLE_MAX_CHARS = 120       # trim bead title in the output section

# Resolve open_brain.py location — mirrors brain_hook.py's resolution.
# Post-install: both files in ~/.claude/hooks/ (same directory).
OPEN_BRAIN_SCRIPT = Path(__file__).parent / "open_brain.py"
if not OPEN_BRAIN_SCRIPT.exists():
    # Dev/repo layout: scripts/hooks/auto_recall_hook.py → scripts/open_brain.py
    OPEN_BRAIN_SCRIPT = Path(__file__).parent.parent / "open_brain.py"


# ─── Trigger logic ────────────────────────────────────────────────────────────

def _should_fire(prompt: str) -> bool:
    """Decide whether the prompt is substantive enough to recall against.

    Fires when length >= MIN_PROMPT_LENGTH AND (length >= LONG_PROMPT_BYPASS
    OR any trigger keyword present, case-insensitive). The bypass exists so
    long substantive prompts always recall; short prompts must explicitly
    signal substantive work via a trigger word.
    """
    if not prompt:
        return False
    length = len(prompt)
    if length < MIN_PROMPT_LENGTH:
        return False
    if length >= LONG_PROMPT_BYPASS:
        return True
    lower = prompt.lower()
    return any(kw in lower for kw in TRIGGER_KEYWORDS)


# ─── Brain search ─────────────────────────────────────────────────────────────

def _run_brain_search(query: str) -> Optional[List[dict]]:
    """Invoke open_brain.py --search and return parsed atom list, or None on any error.

    The HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE env vars suppress the
    HuggingFace Hub warning + occasional client-closed errors when many
    captures fire rapid-fire.
    """
    if not OPEN_BRAIN_SCRIPT.exists():
        return None

    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}

    try:
        proc = subprocess.run(
            [
                "python3", str(OPEN_BRAIN_SCRIPT),
                "--search", query[:QUERY_WINDOW_CHARS],
                "--limit", str(RECALL_LIMIT),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

    if proc.returncode != 0:
        return None

    stdout = proc.stdout or ""
    if not stdout.strip():
        return None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    except Exception:
        return None

    if not isinstance(data, list):
        return None

    return data


# ─── Output formatting ────────────────────────────────────────────────────────

def _format_atom_line(atom: dict) -> Optional[str]:
    """Render one atom as a markdown bullet line, or None if malformed."""
    try:
        thought_id = str(atom.get("THOUGHT_ID") or "")
        if not thought_id:
            return None
        short_id = thought_id[-SHORT_ID_CHARS:] if len(thought_id) >= SHORT_ID_CHARS else thought_id

        created_at = str(atom.get("CREATED_AT") or "")
        date = created_at[:DATE_CHARS] if created_at else "????-??-??"

        thought_type = str(atom.get("THOUGHT_TYPE") or "unknown")

        summary = str(atom.get("SUMMARY") or "").strip()
        if not summary:
            summary = "(no summary)"
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "..."

        return f"- {date} | {thought_type} | {short_id} — {summary}"
    except Exception:
        return None


def _build_additional_context(atoms: List[dict]) -> Optional[str]:
    """Build the additionalContext markdown body from a deduped atom list.

    Dedup keeps the first occurrence of each THOUGHT_ID. Returns None if no
    valid atoms render (so the caller emits NO stdout rather than an empty
    block).
    """
    seen_ids = set()
    lines: List[str] = []
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        thought_id = atom.get("THOUGHT_ID")
        if not thought_id or thought_id in seen_ids:
            continue
        line = _format_atom_line(atom)
        if line is None:
            continue
        seen_ids.add(thought_id)
        lines.append(line)

    if not lines:
        return None

    header = "## Recent neurosymbolic context\n\n### Related prior memories (top {n})".format(
        n=len(lines),
    )
    body = "\n".join(lines)
    return f"{header}\n{body}"


# ─── Stale-state guard — bead lookups ─────────────────────────────────────────

def _extract_bead_ids(prompt: str) -> List[str]:
    """Find unique bead IDs in the prompt, preserving first-seen order, capped.

    Pattern: lower-case ``gz-`` followed by 4-8 alphanumeric chars. Dedup by
    first occurrence; cap at BEAD_ID_CAP to bound subprocess cost.
    """
    seen = set()
    ordered: List[str] = []
    for match in BEAD_ID_PATTERN.finditer(prompt):
        bid = match.group(0)
        if bid in seen:
            continue
        seen.add(bid)
        ordered.append(bid)
        if len(ordered) >= BEAD_ID_CAP:
            break
    return ordered


def _run_beads_show(bead_id: str) -> Optional[dict]:
    """Call ``beads show <id> --json`` and return parsed dict, or None on any error."""
    try:
        proc = subprocess.run(
            ["beads", "show", bead_id, "--json"],
            capture_output=True,
            text=True,
            timeout=BEADS_SHOW_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

    if proc.returncode != 0:
        return None
    stdout = proc.stdout or ""
    if not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    return data


def _collect_stale_beads(bead_ids: List[str]) -> List[Tuple[str, str, str]]:
    """For each ID, look up via beads show; return only stale (closed/done) ones.

    Returns list of ``(bead_id, status, title)`` tuples. Honors the wall-clock
    budget — short-circuits the loop if total elapsed time exceeds
    STALE_GUARD_BUDGET_SECONDS, emitting whatever was already collected.
    """
    stale: List[Tuple[str, str, str]] = []
    start = time.monotonic()
    for bid in bead_ids:
        if time.monotonic() - start > STALE_GUARD_BUDGET_SECONDS:
            break
        data = _run_beads_show(bid)
        if not data:
            continue
        status = str(data.get("status") or "").strip().lower()
        if status not in STALE_BEAD_STATES:
            continue
        title = str(data.get("title") or "").strip()
        if len(title) > BEAD_TITLE_MAX_CHARS:
            title = title[:BEAD_TITLE_MAX_CHARS].rstrip() + "..."
        stale.append((bid, status, title or "(no title)"))
    return stale


# ─── Stale-state guard — atom supersession lookups ────────────────────────────

def _extract_atom_ids(prompt: str) -> List[str]:
    """Find unique brain atom IDs in the prompt, preserving order, capped.

    Pattern: ``brain-<digits>-<hex>``. Dedup; cap at ATOM_ID_CAP.
    """
    seen = set()
    ordered: List[str] = []
    for match in ATOM_ID_PATTERN.finditer(prompt):
        aid = match.group(0)
        if aid in seen:
            continue
        seen.add(aid)
        ordered.append(aid)
        if len(ordered) >= ATOM_ID_CAP:
            break
    return ordered


def _run_open_brain_inspect(atom_id: str) -> Optional[dict]:
    """Call ``open_brain.py --inspect <id> --json`` and return parsed dict, or None on error."""
    if not OPEN_BRAIN_SCRIPT.exists():
        return None
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    try:
        proc = subprocess.run(
            ["python3", str(OPEN_BRAIN_SCRIPT), "--inspect", atom_id, "--json"],
            capture_output=True,
            text=True,
            timeout=ATOM_INSPECT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

    if proc.returncode != 0:
        return None
    stdout = proc.stdout or ""
    if not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    return data


def _extract_atom_supersession(data: dict) -> Optional[str]:
    """Inspect the parsed atom payload for superseded_by / forgotten_at markers.

    Returns a short human-readable reason string if the atom is stale, else
    None. Tolerates several shapes since open_brain --inspect may wrap the
    actual atom under a ``result`` key or surface ``prov`` at the top level.
    """
    candidates = [data]
    result = data.get("result")
    if isinstance(result, dict):
        candidates.append(result)

    for payload in candidates:
        prov = payload.get("prov")
        if isinstance(prov, dict):
            superseded = prov.get("superseded_by")
            forgotten = prov.get("forgotten_at")
            if superseded:
                return f"superseded_by={superseded}"
            if forgotten:
                return f"forgotten_at={forgotten}"
        # Some shapes surface these at top level rather than nested under prov.
        superseded = payload.get("superseded_by")
        forgotten = payload.get("forgotten_at")
        if superseded:
            return f"superseded_by={superseded}"
        if forgotten:
            return f"forgotten_at={forgotten}"
    return None


def _collect_stale_atoms(atom_ids: List[str]) -> List[Tuple[str, str]]:
    """For each atom ID, look up via open_brain --inspect; surface only stale ones.

    Returns ``(atom_id, reason)`` tuples. Atoms without supersession/forget
    metadata (the vast majority) are silently skipped — no warning needed.
    """
    stale: List[Tuple[str, str]] = []
    for aid in atom_ids:
        data = _run_open_brain_inspect(aid)
        if not data:
            continue
        reason = _extract_atom_supersession(data)
        if reason is None:
            continue
        stale.append((aid, reason))
    return stale


# ─── Stale-state guard — section formatting ───────────────────────────────────

def _format_stale_guard_section(
    stale_beads: List[Tuple[str, str, str]],
    stale_atoms: List[Tuple[str, str]],
) -> Optional[str]:
    """Render the stale-state guard markdown block, or None if nothing to show.

    Suppressed entirely when both lists are empty — no false-alarm header.
    """
    if not stale_beads and not stale_atoms:
        return None

    lines: List[str] = ["## Stale-state guard"]
    if stale_beads:
        lines.append("")
        lines.append(
            "The following work-items referenced in your prompt are already "
            "CLOSED — verify your prompt's assumption against current state "
            "before acting:"
        )
        for bid, status, title in stale_beads:
            lines.append(f"- {bid} [{status}] — {title}")
    if stale_atoms:
        lines.append("")
        lines.append(
            "The following recalled brain atoms referenced in your prompt may "
            "be stale — superseded or forgotten since capture:"
        )
        for aid, reason in stale_atoms:
            lines.append(f"- {aid} — {reason}")
    return "\n".join(lines)


def _build_stale_guard_for_prompt(prompt: str) -> Optional[str]:
    """End-to-end stale-state guard pipeline for a single prompt.

    Wraps extraction + lookup + formatting in a fail-open envelope so any
    exception (subprocess crash, parse failure, etc) yields None — the recall
    block keeps emitting unaffected.
    """
    try:
        bead_ids = _extract_bead_ids(prompt)
        atom_ids = _extract_atom_ids(prompt)
        if not bead_ids and not atom_ids:
            return None
        stale_beads = _collect_stale_beads(bead_ids) if bead_ids else []
        stale_atoms = _collect_stale_atoms(atom_ids) if atom_ids else []
        return _format_stale_guard_section(stale_beads, stale_atoms)
    except Exception:
        return None


# ─── Hook stdin protocol ──────────────────────────────────────────────────────

def _read_prompt_from_stdin() -> str:
    """Parse the user prompt from the Claude Code hook stdin envelope.

    Be defensive — accept either ``prompt`` or ``user_prompt`` field name,
    fall back to empty string. Any parse failure yields empty (which then
    silently exits).
    """
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return ""
    except Exception:
        return ""

    if not isinstance(event, dict):
        return ""

    prompt = event.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        prompt = event.get("user_prompt")
    if not isinstance(prompt, str):
        return ""
    return prompt


# ─── Main entry point ─────────────────────────────────────────────────────────

def main() -> None:
    """Hook entry point. Always exits 0; emits stdout only when recall succeeds.

    Composition: the recall block (semantic prior-memories pull) runs first.
    The stale-state guard then runs independently — its job is to surface
    referenced bead-IDs that are already closed and recalled atoms that have
    been superseded. If the recall block is silent (trigger missed or empty
    result), the guard still runs against any IDs found in the prompt. Either
    block alone is sufficient to emit additionalContext; both blocks together
    are concatenated recall-first.
    """
    try:
        prompt = _read_prompt_from_stdin()
        if not prompt:
            return

        recall_block: Optional[str] = None
        if _should_fire(prompt):
            atoms = _run_brain_search(prompt)
            if atoms:
                recall_block = _build_additional_context(atoms)

        stale_block = _build_stale_guard_for_prompt(prompt)

        if recall_block and stale_block:
            context = f"{recall_block}\n\n{stale_block}"
        elif recall_block:
            context = recall_block
        elif stale_block:
            context = stale_block
        else:
            return

        # Emit the additionalContext envelope. Claude Code prepends this to the
        # agent's reasoning frame before the prompt is processed.
        sys.stdout.write(json.dumps({"additionalContext": context}))
    except Exception:
        # Fail-open catchall: never block a user prompt.
        return


if __name__ == "__main__":
    main()
