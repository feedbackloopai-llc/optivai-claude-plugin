#!/usr/bin/env python3
"""
Till-Done Hook for Claude Code — task-driven blocking via PreToolUse.

When enabled, blocks tool execution unless the agent has active tasks
managed via the TodoWrite/TodoRead tools.

State stored in ~/.claude/tilldone.json:
  { "enabled": true, "tasks": [...] }

Hook protocol:
  stdin: JSON with tool_name, tool_input
  stdout: JSON with "decision": "approve" | "block", "reason": "..."

Enable/disable via /tilldone command or editing the state file.
"""

import sys
import json
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "tilldone.json"

# Tools exempt from blocking (observation + task management)
EXEMPT_TOOLS = {
    "Read", "Grep", "Glob", "LS",
    "TodoRead", "TodoWrite",
    "AskUserQuestion",
    "SlashCommand",
    "WebSearch", "WebFetch",
}


def load_state() -> dict:
    """Load till-done state from disk."""
    if not STATE_FILE.exists():
        return {"enabled": False, "tasks": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"enabled": False, "tasks": []}


def save_state(state: dict) -> None:
    """Save till-done state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def main():
    state = load_state()

    # If not enabled, approve everything
    if not state.get("enabled", False):
        print(json.dumps({"decision": "approve"}))
        return

    # Read hook input
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, IOError):
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = hook_input.get("tool_name", "")

    # Exempt tools always pass
    if tool_name in EXEMPT_TOOLS:
        # If TodoWrite, update our task state
        if tool_name == "TodoWrite":
            todos = hook_input.get("tool_input", {}).get("todos", [])
            if todos:
                state["tasks"] = todos
                save_state(state)
        print(json.dumps({"decision": "approve"}))
        return

    # Check task state
    tasks = state.get("tasks", [])

    if not tasks:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "⛔ Till-Done: No active tasks. "
                "Use TodoWrite to create a task list before proceeding. "
                "Example: Add tasks describing what you plan to do, then mark them in_progress."
            ),
        }))
        return

    # Check if any task is in_progress
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    all_done = all(t.get("status") in ("completed", "done") for t in tasks)

    if all_done:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "⛔ Till-Done: All tasks are complete. "
                "Use TodoWrite to clear the list or add new tasks before proceeding."
            ),
        }))
        return

    if not in_progress:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "⛔ Till-Done: No task is in progress. "
                "Use TodoWrite to mark a task as 'in_progress' before using other tools."
            ),
        }))
        return

    # Active task exists — approve
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
