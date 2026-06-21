#!/usr/bin/env python3
"""Claude Code statusline — shows an active OptivAI Loop indicator (OBS3).

Reads ``~/.claude/loop-state.json`` (written by loop_runner.py, OBS2). When a
loop is active AND fresh (updated within STALE_S seconds), renders:

    🔄 <molecule> <iter>/<max> · <closed>✓[ <failed>✗]  ·  <dir>

Otherwise falls back to a minimal default (the cwd basename) parsed from the
session JSON Claude Code pipes on stdin. Fail-open: any error → minimal/empty,
never a traceback in the statusline.

Wire via settings.json:
    "statusLine": {"type": "command", "command": "python3 ~/.claude/loop-statusline.py"}

The 120s staleness guard means a finished or crashed loop disappears from the
statusline on its own — it does not linger.
"""
import json
import os
import sys
import time

STATE_PATH = os.path.expanduser("~/.claude/loop-state.json")
STALE_S = 120


def _loop_segment():
    """Return the loop indicator string, or None when no fresh active loop.

    Multi-worker (Mayor) format — emitted when ``active_workers`` key is present:
      🔄 <molecule> <closed>/<total> · <active>w <recovery_blocked>⚠ · <closed>✓ <failed>✗

    Single-worker (legacy run_loop) format — emitted when ``active_workers`` is absent:
      🔄 <molecule> <iter>/<max> · <closed>✓ [<failed>✗]

    Preserves the 120s staleness guard and fail-open: any exception → None (never raises).
    """
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if not d.get("active"):
            return None
        if time.time() - float(d.get("updated_at", 0)) > STALE_S:
            return None
        molecule = d.get("molecule", "?")
        closed = d.get("closed", 0)
        failed = d.get("failed", 0)
        if "active_workers" in d:
            # Mayor multi-worker render
            cap = d.get("capacity", {})
            n_active = cap.get("active", len(d["active_workers"]))
            n_recovery = cap.get("recovery_blocked", 0)
            total = closed + failed
            seg = (
                f"🔄 {molecule} {closed}/{total} "
                f"· {n_active}w"
            )
            if n_recovery:
                seg += f" {n_recovery}⚠"
            seg += f" · {closed}✓"
            if failed:
                seg += f" {failed}✗"
        else:
            # Legacy single-worker render (backward compat)
            iteration = d.get("iteration", 0)
            max_iterations = d.get("max_iterations", 0)
            seg = (
                f"🔄 {molecule} "
                f"{iteration}/{max_iterations} "
                f"· {closed}✓"
            )
            if failed:
                seg += f" {failed}✗"
        return seg
    except Exception:
        return None


def _default(stdin_json):
    """Minimal, non-intrusive default: the working-dir basename."""
    try:
        cwd = (
            (stdin_json.get("workspace", {}) or {}).get("current_dir")
            or stdin_json.get("cwd")
            or os.getcwd()
        )
        return os.path.basename(str(cwd).rstrip("/")) or "~"
    except Exception:
        return ""


def main():
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass
    try:
        sj = json.loads(raw) if raw.strip() else {}
    except Exception:
        sj = {}

    loop = _loop_segment()
    if loop:
        print(f"{loop}  ·  {_default(sj)}")
    else:
        print(_default(sj))


if __name__ == "__main__":
    main()
