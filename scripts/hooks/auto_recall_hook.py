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
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

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
    """Hook entry point. Always exits 0; emits stdout only when recall succeeds."""
    try:
        prompt = _read_prompt_from_stdin()
        if not prompt:
            return

        if not _should_fire(prompt):
            return

        atoms = _run_brain_search(prompt)
        if not atoms:  # None or empty list → silent
            return

        context = _build_additional_context(atoms)
        if not context:
            return

        # Emit the additionalContext envelope. Claude Code prepends this to the
        # agent's reasoning frame before the prompt is processed.
        sys.stdout.write(json.dumps({"additionalContext": context}))
    except Exception:
        # Fail-open catchall: never block a user prompt.
        return


if __name__ == "__main__":
    main()
