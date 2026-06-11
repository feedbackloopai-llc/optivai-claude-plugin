#!/usr/bin/env python3
"""scripts/hooks/post_tool_use.py — L6b egress redaction hook.

PROTOCOL NOTE (honest account of what this hook can and cannot do):

Claude Code's PostToolUse hook receives the tool result via stdin as a JSON
envelope with shape::

    {
        "tool_name": "Bash",
        "tool_input": {...},
        "tool_result": "<the raw tool output as a string>"
    }

The hook can emit JSON to stdout (e.g. ``{"hookSpecificOutput": {...}}``) for
side-effect metadata, but there is NO supported mechanism for a PostToolUse
hook command to mutate the ``tool_result`` that the model has already received.
The model sees the unredacted output regardless of what this hook writes.

What this hook DOES:
  1. Parse the PostToolUse envelope from stdin.
  2. Extract ``tool_result`` (also accepts ``tool_response`` defensively).
  3. Run the composed redaction pipeline (same pipeline used by
     open_brain.py / memory_writer.py / beads_writer.py).
  4. Write the redacted version to the activity log as an "output" entry so
     that the .claude/logs/*.jsonl files and any downstream pg-sync contain
     the redacted result, not the raw credential.
  5. Emit exit 0 with no stdout (so Claude Code continues normally).

This closes the egress gap for PERSISTENCE: a Bash/Read that returns a
credential is logged UNREDACTED in .claude/logs unless this hook fires.
It does NOT prevent the model from seeing the credential in its context
window — that is an in-context guardrail problem outside this hook's scope.

Fail-open contract: any error (bad JSON, missing field, import failure,
log write failure) exits 0 silently. This hook must never block a session.

Bead: fblai-7sjjj (FIX 1).
"""
import json
import os
import sys
import time
from pathlib import Path


def _get_scripts_dir() -> str:
    """Return the scripts/ directory regardless of install vs dev layout."""
    # Installed: ~/.claude/hooks/post_tool_use.py → scripts_dir = ~/.claude/hooks
    # Dev/repo: scripts/hooks/post_tool_use.py → scripts_dir = scripts/
    hook_dir = os.path.dirname(os.path.abspath(__file__))
    # Try the parent (dev layout: scripts/hooks/ → scripts/).
    parent = os.path.normpath(os.path.join(hook_dir, ".."))
    # The scripts dir has redact/ inside it; prefer the parent when it does.
    if os.path.isdir(os.path.join(parent, "redact")):
        return parent
    # Installed layout: hooks/ dir itself may contain redact/ if vendored there.
    if os.path.isdir(os.path.join(hook_dir, "redact")):
        return hook_dir
    # Fall back to parent so import attempts are at least attempted.
    return parent


def _redact(text: str) -> str:
    """Run the default redaction pipeline over *text*. Returns original on any failure."""
    try:
        scripts_dir = _get_scripts_dir()
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from redact.default_pipeline import redact_pii  # type: ignore
        result = redact_pii(text)
        if result is None:
            return text
        return result
    except Exception:
        return text


def _write_output_log(tool_name: str, redacted_output: str) -> None:
    """Append a redacted-output record to the activity log.

    Mirrors the log format used by log_writer.py so the JSONL file stays
    consistent. Writes to .claude/logs/activity.jsonl in cwd (per-project)
    or ~/.claude/logs/activity.jsonl as fallback.
    """
    try:
        log_dir = Path.cwd() / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "activity.jsonl"

        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "PostToolUse",
            "tool_name": tool_name,
            "redacted_output_length": len(redacted_output),
            # Store only first 2000 chars; prevents enormous bash outputs
            # from ballooning logs while still capturing any credential hit.
            "redacted_output_preview": redacted_output[:2000],
        }
        with open(str(log_path), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        # Log write failure must not surface to the session.
        pass


def main() -> None:
    """PostToolUse hook entry point. Always exits 0.

    Reads the PostToolUse JSON envelope from stdin, extracts the tool
    result, redacts PII/secrets via the default pipeline, and persists the
    redacted version to the activity log. Never raises; never writes stdout
    (which would be interpreted as hook output by Claude Code).
    """
    # Step 1: Read stdin. Any failure → silent exit.
    try:
        raw = sys.stdin.read()
    except Exception:
        return

    if not raw or not raw.strip():
        return

    # Step 2: Parse the PostToolUse envelope.
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Malformed JSON — fail-open, do nothing.
        return

    if not isinstance(envelope, dict):
        return

    tool_name = str(envelope.get("tool_name") or "unknown")

    # The field name in the envelope is "tool_result" (confirmed from docs and
    # hookify reference implementation). Accept "tool_response" defensively in
    # case future Claude Code versions rename it.
    tool_result = envelope.get("tool_result")
    if tool_result is None:
        tool_result = envelope.get("tool_response")
    if tool_result is None:
        # No output field present — nothing to redact.
        return

    # Step 3: Coerce to string. Tool results may be str or structured.
    if not isinstance(tool_result, str):
        try:
            tool_result = json.dumps(tool_result)
        except Exception:
            return

    if not tool_result:
        return

    # Step 4: Redact. Fail-open: if redaction crashes, use original.
    try:
        redacted = _redact(tool_result)
    except Exception:
        redacted = tool_result

    # Step 5: Write the redacted output to the activity log.
    # This is the persistence interception point — the log gets the redacted
    # version rather than the raw credential-bearing string.
    _write_output_log(tool_name, redacted)

    # Step 6: Exit 0 with no stdout. Claude Code reads stdout for hook
    # directives (e.g. {"decision": "..."}); emitting nothing is the
    # correct "observer only, no intervention" contract.


if __name__ == "__main__":
    main()
