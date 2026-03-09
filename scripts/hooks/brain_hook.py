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

# ─── Trigger patterns ─────────────────────────────────────────────────────────

# User prompt triggers — organized by signal type
PROMPT_TRIGGERS = [
    # Explicit capture requests
    re.compile(r"\bremember (this|that)\b", re.IGNORECASE),
    re.compile(r"\bcapture (this|that) thought\b", re.IGNORECASE),
    re.compile(r"\bnote to self\b", re.IGNORECASE),

    # Decision signals
    re.compile(r"\bi decided\b", re.IGNORECASE),
    re.compile(r"\bkey (decision|insight|takeaway)\b", re.IGNORECASE),
    re.compile(r"\blet'?s go with\b", re.IGNORECASE),
    re.compile(r"\bwe('re| are) going (to|with)\b", re.IGNORECASE),

    # Preference signals
    re.compile(r"\balways (do|use|prefer)\b", re.IGNORECASE),
    re.compile(r"\bnever (do|use)\b", re.IGNORECASE),
    re.compile(r"\bi prefer\b", re.IGNORECASE),
    re.compile(r"\bfrom now on\b", re.IGNORECASE),

    # Meeting / people signals
    re.compile(r"\bmeeting (note|summary)\b", re.IGNORECASE),
    re.compile(r"\btalked (to|with)\b", re.IGNORECASE),
    re.compile(r"\b(dave|sarah|jeff|chris|chandler|marshall)\b.*\bsaid\b", re.IGNORECASE),

    # Pattern / learning signals
    re.compile(r"\blessons? learned\b", re.IGNORECASE),
    re.compile(r"\bwhat worked\b", re.IGNORECASE),
    re.compile(r"\bnext time\b.*\bshould\b", re.IGNORECASE),
    re.compile(r"\bgotcha\b", re.IGNORECASE),
]

# File path patterns that indicate decision/architecture docs
DECISION_FILE_PATTERNS = [
    re.compile(r"docs/plans/", re.IGNORECASE),
    re.compile(r"docs/decisions/", re.IGNORECASE),
    re.compile(r"docs/adr/", re.IGNORECASE),
    re.compile(r"ARCHITECTURE\.md$", re.IGNORECASE),
    re.compile(r"DECISION.*\.md$", re.IGNORECASE),
]


# ─── Capture logic ────────────────────────────────────────────────────────────

def _fire_capture(text: str, source: str, session_id: str, project: str) -> None:
    """Fire-and-forget capture to persistent memory."""
    if not BRAIN_SCRIPT.exists():
        return
    if not text or len(text.strip()) < 20:
        return

    try:
        payload = json.dumps({
            "op": "capture",
            "text": text[:4000],
            "source": source,
            "session_id": session_id,
            "project": project,
        })

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
    """Check user prompt for brain trigger phrases."""
    if any(rx.search(prompt) for rx in PROMPT_TRIGGERS):
        _fire_capture(
            text=prompt,
            source="claude-code-auto",
            session_id=session_id,
            project=project,
        )


def on_tool_use(
    operation: str,
    prompt: str,
    details: dict,
    session_id: str,
    project: str,
    **_kwargs,
) -> None:
    """Check tool operations for auto-capture triggers."""

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
                source="claude-code-hook",
                session_id=session_id,
                project=project,
            )
