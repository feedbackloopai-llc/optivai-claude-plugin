#!/usr/bin/env python3
"""
Brain Hook — Auto-captures significant operations to persistent memory.

Called from pre-tool-use.py and user-prompt-submit.py.
Watches for decisions, preferences, patterns, people signals, and trigger phrases,
then fires an async capture to PostgreSQL via open_brain.py.

This is fire-and-forget — never blocks the hook pipeline.
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional

# Resolve open_brain.py location
# Post-install: both files in ~/.claude/hooks/ (same directory)
BRAIN_SCRIPT = Path(__file__).parent / "open_brain.py"
if not BRAIN_SCRIPT.exists():
    # Dev/repo layout: scripts/hooks/brain_hook.py → scripts/open_brain.py
    BRAIN_SCRIPT = Path(__file__).parent.parent / "open_brain.py"

# ─── L3 ingest redaction (redact-S8) ──────────────────────────────────────────
# Redact secrets / PII BEFORE the auto-capture payload is built. Defensive
# import: if the redact module is missing or fails to load (e.g. during a
# partial install or before Bundle D landed), fall back to a passthrough so
# the auto-capture path keeps working. Fail-open is the explicit contract here:
# better to capture an un-redacted thought than to lose the memory entirely.
try:
    _scripts_dir = str(Path(__file__).resolve().parent.parent)
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from open_brain import redact_pii as _redact_pii  # type: ignore
except Exception:
    def _redact_pii(text):  # type: ignore[no-redef]
        return text  # fail-open passthrough

# ─── Trigger patterns ─────────────────────────────────────────────────────────

# User prompt triggers — organized by signal type. Each (pattern, label) pair
# lets us emit a PROV-specific trigger name into prov_agent / prov_activity
# (e.g. `claude-code-hook-decision-signal`) rather than a single opaque
# "claude-code-auto" agent across all auto-captures. The label is matched
# against the FIRST regex that hits; ordering matters only inasmuch as more
# specific patterns should precede broader ones within the same category.
PROMPT_TRIGGER_RULES = [
    # Explicit capture requests
    (re.compile(r"\bremember (this|that)\b", re.IGNORECASE), "explicit-request"),
    (re.compile(r"\bcapture (this|that) thought\b", re.IGNORECASE), "explicit-request"),
    (re.compile(r"\bnote to self\b", re.IGNORECASE), "explicit-request"),

    # Decision signals
    (re.compile(r"\bi decided\b", re.IGNORECASE), "decision-signal"),
    (re.compile(r"\bkey (decision|insight|takeaway)\b", re.IGNORECASE), "decision-signal"),
    (re.compile(r"\blet'?s go with\b", re.IGNORECASE), "decision-signal"),
    (re.compile(r"\bwe('re| are) going (to|with)\b", re.IGNORECASE), "decision-signal"),

    # Preference signals
    (re.compile(r"\balways (do|use|prefer)\b", re.IGNORECASE), "preference"),
    (re.compile(r"\bnever (do|use)\b", re.IGNORECASE), "preference"),
    (re.compile(r"\bi prefer\b", re.IGNORECASE), "preference"),
    (re.compile(r"\bfrom now on\b", re.IGNORECASE), "preference"),

    # Meeting / people signals
    (re.compile(r"\bmeeting (note|summary)\b", re.IGNORECASE), "people-signal"),
    (re.compile(r"\btalked (to|with)\b", re.IGNORECASE), "people-signal"),
    (re.compile(r"\b(dave|sarah|jeff|chris|chandler|marshall)\b.*\bsaid\b", re.IGNORECASE), "people-signal"),

    # Pattern / learning signals
    (re.compile(r"\blessons? learned\b", re.IGNORECASE), "learning"),
    (re.compile(r"\bwhat worked\b", re.IGNORECASE), "learning"),
    (re.compile(r"\bnext time\b.*\bshould\b", re.IGNORECASE), "learning"),
    (re.compile(r"\bgotcha\b", re.IGNORECASE), "learning"),
]

# Back-compat: a flat list of compiled regexes for any caller still iterating
# the old PROMPT_TRIGGERS name. The label-aware logic lives in PROMPT_TRIGGER_RULES.
PROMPT_TRIGGERS = [rx for rx, _label in PROMPT_TRIGGER_RULES]


def _match_trigger_label(prompt: str) -> Optional[str]:
    """Return the label of the first matching trigger rule, or None."""
    for rx, label in PROMPT_TRIGGER_RULES:
        if rx.search(prompt):
            return label
    return None

# File path patterns that indicate decision/architecture docs
DECISION_FILE_PATTERNS = [
    re.compile(r"docs/plans/", re.IGNORECASE),
    re.compile(r"docs/decisions/", re.IGNORECASE),
    re.compile(r"docs/adr/", re.IGNORECASE),
    re.compile(r"ARCHITECTURE\.md$", re.IGNORECASE),
    re.compile(r"DECISION.*\.md$", re.IGNORECASE),
]


# ─── Capture logic ────────────────────────────────────────────────────────────

def _fire_capture(
    text: str,
    source: str,
    session_id: str,
    project: str,
    prov_agent: Optional[str] = None,
    prov_activity: Optional[str] = None,
) -> None:
    """Fire-and-forget capture to persistent memory.

    The optional ``prov_agent`` and ``prov_activity`` arguments are forwarded
    to open_brain.py's ``--from-pi`` bridge so hook-driven captures get a
    distinct PROV-DM identity (e.g. ``claude-code-hook-decision-signal`` /
    ``auto-capture-decision-signal``) rather than collapsing into the
    default ``cli-user-*`` agent.
    """
    if not BRAIN_SCRIPT.exists():
        return
    if not text or len(text.strip()) < 20:
        return

    # L3 ingest redaction: strip secrets / PII BEFORE the payload is built so
    # the bridge never sees the raw text. Fail-open — if the redactor raises
    # for any reason, fall back to the original text. A missed redaction can
    # be cleaned up later via /brain-forget; a broken auto-capture loses the
    # memory entirely. The trade-off favors capture completeness.
    try:
        safe_text = _redact_pii(text) if text else text
        if safe_text is None:
            safe_text = text
    except Exception:
        safe_text = text

    try:
        payload_obj = {
            "op": "capture",
            "text": safe_text[:4000],
            "source": source,
            "session_id": session_id,
            "project": project,
        }
        if prov_agent is not None:
            payload_obj["prov_agent"] = prov_agent
        if prov_activity is not None:
            payload_obj["prov_activity"] = prov_activity
        payload = json.dumps(payload_obj)

        proc = subprocess.Popen(
            [sys.executable, str(BRAIN_SCRIPT), "--from-pi"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.stdin.write(payload.encode("utf-8"))
        proc.stdin.close()
        # Don't wait — fire and forget
    except Exception:
        pass  # Never fail the hook pipeline


# ─── Public API (called from pre-tool-use.py and user-prompt-submit.py) ───────

def on_user_prompt(
    prompt: str,
    session_id: str,
    project: str,
) -> None:
    """Check user prompt for brain trigger phrases.

    Emits PROV-distinct captures: the trigger label (decision-signal /
    preference / people-signal / learning / explicit-request) becomes both
    the source suffix and the prov_activity suffix, so downstream queries
    can filter auto-captures by what fired them.
    """
    label = _match_trigger_label(prompt)
    if label is None:
        return
    _fire_capture(
        text=prompt,
        source=f"hook-{label}",
        session_id=session_id,
        project=project,
        prov_agent=f"claude-code-hook-{label}",
        prov_activity=f"auto-capture-{label}",
    )


def on_tool_use(
    operation: str,
    prompt: str,
    details: dict,
    session_id: str,
    project: str,
    **_kwargs,
) -> None:
    """Check tool operations for auto-capture triggers.

    Decision-doc writes are tagged ``claude-code-hook-decision-doc`` /
    ``auto-capture-decision-doc`` so they're distinguishable from prompt-
    pattern auto-captures in PROV queries.
    """
    # Write/edit to decision documents → capture the content
    if operation in ("write", "edit"):
        file_path = details.get("file_path", "") or details.get("path", "")
        if any(rx.search(file_path) for rx in DECISION_FILE_PATTERNS):
            summary = f"Updated decision doc: {file_path}"
            content = details.get("content", "") or details.get("new_text", "")
            if content:
                summary += f"\n\nContent preview:\n{content[:500]}"
            _fire_capture(
                text=summary,
                source="hook-decision-doc",
                session_id=session_id,
                project=project,
                prov_agent="claude-code-hook-decision-doc",
                prov_activity="auto-capture-decision-doc",
            )
