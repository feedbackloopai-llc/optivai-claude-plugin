#!/usr/bin/env python3
"""scripts/hooks/post_tool_use.py — L6b egress redaction hook.

Wraps tool / model output BEFORE it reaches downstream consumers (logs,
memory, replay-log, beads). Reads tool output from stdin, applies the
composed gz-redact-derived pipeline, writes redacted output to stdout.

Hook contract (matches Claude Code hooks):
    cat tool_output | python3 ~/.claude/hooks/post_tool_use.py > redacted_output

Fail-open: if redaction crashes for any reason, original text passes
through. The agent's tool pipeline must not be blocked by a redaction
failure.

Bead: redact-S9.
"""
import os
import sys


def main() -> None:
    """Read stdin, redact, write stdout. Never raises.

    The function is structured as nested try/except blocks so that EVERY
    failure mode — stdin read error, import error, redactor crash, stdout
    write error — falls back to the safest action (passthrough or no-op).
    """
    # Step 1: Read stdin. If we cannot read stdin at all, exit silently.
    try:
        text = sys.stdin.read()
    except Exception:
        return

    if not text:
        # Empty input → empty output, no work to do.
        return

    # Step 2: Attempt redaction. Any failure falls through to passthrough.
    redacted = text
    try:
        scripts_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        )
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        # Defer import so a stdin-only invocation with empty input doesn't
        # pay the import cost (handled by the early-return above).
        from open_brain import redact_pii  # type: ignore

        result = redact_pii(text)
        if result is not None:
            redacted = result
    except Exception:
        # Fail-open: pass through original text on any failure
        redacted = text

    # Step 3: Write to stdout. If even this fails, swallow it — we are a
    # passive transformer and must never crash the caller's pipeline.
    try:
        sys.stdout.write(redacted)
    except Exception:
        try:
            sys.stdout.write(text)
        except Exception:
            return


if __name__ == "__main__":
    main()
