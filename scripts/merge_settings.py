#!/usr/bin/env python3
"""
merge_settings.py — Settings-aware merge helper for install.sh

CLI:
    python3 merge_settings.py <settings_path> [--email X] [--org Y] [--uninstall]

Behavior (install mode):
  1. Load existing settings.json at <settings_path> (or {} if absent/empty).
     If invalid JSON, back up to <path>.corrupt-<timestamp> and start from {}.
  2. PRESERVE every existing top-level key untouched.
  3. ENV: ensure settings["env"] exists.
     - Set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="1" only if not already present.
     - If --email given and non-empty, set CLAUDE_USER_EMAIL (overwrite ok).
     - If --org given, set CLAUDE_ORG_NAME (overwrite ok).
     - Do NOT delete other env keys.
  4. HOOKS: idempotently ensure the plugin's required hook commands are present
     without removing the user's other hooks.
  5. Write back atomically (write to <path>.tmp then os.replace).

Behavior (--uninstall mode):
  Remove ONLY the plugin's known command strings from settings["hooks"].
  Leave every other hook and every other key untouched.

Python 3.8+ compatible, stdlib-only.
"""

import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Plugin-owned hook commands (the exact strings that install wires)
# ---------------------------------------------------------------------------

# Schema: { event_name: (matcher_or_None, [command_strings]) }
# matcher=None means the group has no "matcher" key (SessionStart, Stop).
PLUGIN_HOOKS: Dict[str, Tuple[Optional[str], List[str]]] = {
    "SessionStart": (
        None,
        ["python3 ~/.claude/hooks/context_primer.py"],
    ),
    "PreToolUse": (
        "*",
        ["python3 ~/.claude/hooks/pre-tool-use.py"],
    ),
    "PostToolUse": (
        "*",
        ["python3 ~/.claude/hooks/post_tool_use.py"],
    ),
    "UserPromptSubmit": (
        "*",
        [
            "python3 ~/.claude/hooks/user-prompt-submit.py",
            "python3 ~/.claude/hooks/auto_recall_hook.py",
        ],
    ),
    "Stop": (
        None,
        [
            "python3 ~/.claude/hooks/session_summary.py",
            'bash "$HOME/.claude/hooks/stop-hook.sh"',
        ],
    ),
}

# ---------------------------------------------------------------------------
# Legacy / variant hook-command forms (upgrade convergence)
# ---------------------------------------------------------------------------
#
# INVARIANT:
#   PLUGIN_HOOKS strings above are the CANONICAL forms. Command-string matching
#   is exact (by design — see _command_present), so when a canonical string
#   changes across plugin versions, the old form is left orphaned in the user's
#   settings.json AND the new form is added — yielding a DUPLICATE hook that
#   fires the same logic twice.
#
#   LEGACY_HOOK_VARIANTS lists historical / alternate spellings for each logical
#   hook. _migrate_legacy_commands() strips any of these variant strings from the
#   hooks groups BEFORE the present-check runs, so the normal add-step then
#   leaves exactly ONE canonical entry per logical hook on upgrade.
#
# FORM RATIONALE (do NOT "normalize ~ and $HOME to one form"):
#   - Python hooks use `~/.claude/hooks/...` because the Claude Code harness
#     expands `~` when it runs the command.
#   - The bash stop-hook uses `bash "$HOME/.claude/hooks/stop-hook.sh"` because
#     `~` does NOT expand inside bash double-quotes — `$HOME` is REQUIRED there.
#   That ~/$HOME asymmetry is semantically necessary and is preserved. The
#   variants below are STALE forms to remove, not a normalization target.
#
# Each entry maps an event -> list of stale command strings to remove.
# (These are historical absolute-path / wrong-expansion forms that earlier
#  installs may have written.)
_HOME = os.path.expanduser("~")
LEGACY_HOOK_VARIANTS: Dict[str, List[str]] = {
    "SessionStart": [
        # absolute-path form
        f"python3 {_HOME}/.claude/hooks/context_primer.py",
        # $HOME-expanded form of a python hook (wrong: harness expands ~)
        "python3 $HOME/.claude/hooks/context_primer.py",
    ],
    "PreToolUse": [
        f"python3 {_HOME}/.claude/hooks/pre-tool-use.py",
        "python3 $HOME/.claude/hooks/pre-tool-use.py",
    ],
    "PostToolUse": [
        f"python3 {_HOME}/.claude/hooks/post_tool_use.py",
        "python3 $HOME/.claude/hooks/post_tool_use.py",
    ],
    "UserPromptSubmit": [
        f"python3 {_HOME}/.claude/hooks/user-prompt-submit.py",
        "python3 $HOME/.claude/hooks/user-prompt-submit.py",
        f"python3 {_HOME}/.claude/hooks/auto_recall_hook.py",
        "python3 $HOME/.claude/hooks/auto_recall_hook.py",
    ],
    "Stop": [
        f"python3 {_HOME}/.claude/hooks/session_summary.py",
        "python3 $HOME/.claude/hooks/session_summary.py",
        # ~-form of the stop-hook: WRONG because ~ does not expand in bash
        # double-quotes, so this would have been a broken path. Strip it.
        'bash "~/.claude/hooks/stop-hook.sh"',
        # absolute-path form of the stop-hook
        f'bash "{_HOME}/.claude/hooks/stop-hook.sh"',
    ],
}


def _migrate_legacy_commands(hooks: Dict[str, Any]) -> List[str]:
    """
    Strip known stale/variant command strings from *hooks* (mutated in place)
    so that the subsequent canonical add yields exactly one entry per logical
    hook on upgrade.

    Returns a list of human-readable log lines for what was migrated away.
    Groups that become empty after stripping are removed; events that become
    empty are removed.
    """
    log: List[str] = []

    for event, variants in LEGACY_HOOK_VARIANTS.items():
        if event not in hooks:
            continue
        variant_set = set(variants)
        event_groups = hooks.get(event)
        if not isinstance(event_groups, list):
            continue

        new_groups: List[Dict[str, Any]] = []
        for group in event_groups:
            if not isinstance(group, dict):
                new_groups.append(group)
                continue
            group_hooks = group.get("hooks", [])
            kept = [
                h
                for h in group_hooks
                if not (isinstance(h, dict) and h.get("command") in variant_set)
            ]
            removed = len(group_hooks) - len(kept)
            if removed:
                log.append(
                    f"hooks.{event}: migrated away {removed} legacy/variant command(s)"
                )
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                new_groups.append(new_group)
            # else: group emptied by migration — drop it

        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]

    return log


# ---------------------------------------------------------------------------
# Pure merge function (no file I/O — unit-testable)
# ---------------------------------------------------------------------------


def merge_settings(
    existing: Dict[str, Any],
    email: Optional[str] = None,
    org: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Merge plugin requirements into *existing* (mutated copy).

    Returns:
        (merged_dict, log_lines)
        log_lines describes what was added vs already-present.
    """
    result = copy.deepcopy(existing)
    log: List[str] = []

    # ── Type coercion (FIX 1) ────────────────────────────────────────────────
    # A user's settings.json may have `"env": null` or `"hooks": null` (valid
    # JSON, but not a dict). setdefault() would then return the existing None and
    # the subsequent `env[key]=...` / `hooks.setdefault(...)` would raise
    # TypeError mid-merge, aborting the install and leaving an orphaned .tmp.
    # Coerce: None → {}; a non-None, non-dict value is a real type error (rare) —
    # treat it as corrupt, warn, and reset to {} so the merge can proceed.
    for key in ("env", "hooks"):
        if key in result:
            val = result[key]
            if val is None:
                result[key] = {}
            elif not isinstance(val, dict):
                log.append(
                    f"WARNING: '{key}' was not an object ({type(val).__name__}); "
                    f"resetting to {{}} (preserving nothing under '{key}')"
                )
                result[key] = {}

    # ── ENV ─────────────────────────────────────────────────────────────────
    env = result.setdefault("env", {})

    if "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env:
        env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        log.append("env: added CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1")
    else:
        log.append("env: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS already set (preserved)")

    if email:
        env["CLAUDE_USER_EMAIL"] = email
        log.append(f"env: set CLAUDE_USER_EMAIL={email}")

    if org:
        env["CLAUDE_ORG_NAME"] = org
        log.append(f"env: set CLAUDE_ORG_NAME={org}")

    # ── HOOKS ────────────────────────────────────────────────────────────────
    hooks = result.setdefault("hooks", {})

    # Strip stale/variant command forms BEFORE the present-check so that an
    # upgrade converges on exactly one canonical entry per logical hook (FIX 2).
    log.extend(_migrate_legacy_commands(hooks))

    for event, (matcher, commands) in PLUGIN_HOOKS.items():
        event_groups: List[Dict[str, Any]] = hooks.setdefault(event, [])

        for cmd_str in commands:
            if _command_present(event_groups, cmd_str):
                log.append(f"hooks.{event}: '{cmd_str}' already present (no-op)")
            else:
                _add_command(event_groups, matcher, cmd_str)
                log.append(f"hooks.{event}: added '{cmd_str}'")

    return result, log


def unmerge_settings(
    existing: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Remove ONLY the plugin's known command strings from settings["hooks"].
    Leaves all other keys and hooks untouched.

    Returns:
        (cleaned_dict, log_lines)
    """
    result = copy.deepcopy(existing)
    log: List[str] = []

    if "hooks" not in result:
        return result, log

    hooks = result["hooks"]
    # Tolerate a `null` or non-dict hooks value: nothing to remove.
    if not isinstance(hooks, dict):
        return result, log

    all_plugin_commands: set = set()
    for _matcher, cmds in PLUGIN_HOOKS.values():
        all_plugin_commands.update(cmds)

    for event in list(hooks.keys()):
        event_groups: List[Dict[str, Any]] = hooks[event]
        new_groups: List[Dict[str, Any]] = []
        for group in event_groups:
            group_hooks = group.get("hooks", [])
            kept = [h for h in group_hooks if h.get("command") not in all_plugin_commands]
            removed_count = len(group_hooks) - len(kept)
            if removed_count:
                log.append(
                    f"hooks.{event}: removed {removed_count} plugin command(s)"
                )
            # Keep group only if it still has hook entries
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                new_groups.append(new_group)
        if new_groups:
            hooks[event] = new_groups
        else:
            # Remove the event key entirely if all groups are now empty
            del hooks[event]
            if not log or f"hooks.{event}" not in log[-1]:
                log.append(f"hooks.{event}: removed (no remaining hooks)")

    if not hooks:
        del result["hooks"]

    return result, log


# ---------------------------------------------------------------------------
# Hook-group helpers
# ---------------------------------------------------------------------------


def _command_present(groups: List[Dict[str, Any]], cmd_str: str) -> bool:
    """Return True if cmd_str appears in any hook entry across all groups."""
    for group in groups:
        for hook_entry in group.get("hooks", []):
            if hook_entry.get("command") == cmd_str:
                return True
    return False


def _add_command(
    groups: List[Dict[str, Any]],
    matcher: Optional[str],
    cmd_str: str,
) -> None:
    """
    Add cmd_str to an appropriate existing group, or append a new group.

    Preference: find an existing group whose matcher matches (or both None).
    If none found, append a new group.
    """
    # Try to find an existing group with the matching matcher
    for group in groups:
        group_matcher = group.get("matcher")
        if group_matcher == matcher:
            group.setdefault("hooks", []).append(
                {"type": "command", "command": cmd_str}
            )
            return

    # No matching group found — create a new one
    new_group: Dict[str, Any] = {"hooks": [{"type": "command", "command": cmd_str}]}
    if matcher is not None:
        new_group["matcher"] = matcher
        # Insert at front so matcher groups are easy to find; but we add at end
        # to avoid disturbing any ordering the user has established.
    groups.append(new_group)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def load_settings(path: str) -> Tuple[Dict[str, Any], bool]:
    """
    Load settings JSON from *path*.

    Returns (data_dict, was_fresh_start).
    was_fresh_start=True if file was absent, empty, or invalid (backed up).
    """
    if not os.path.exists(path):
        return {}, True

    raw = ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        print(f"WARNING: could not read {path}: {exc}", file=sys.stderr)
        return {}, True

    if not raw:
        return {}, True

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-level JSON value is not an object")
        return data, False
    except (json.JSONDecodeError, ValueError) as exc:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.corrupt-{ts}"
        try:
            shutil.copy2(path, backup)
            print(
                f"WARNING: {path} contained invalid JSON ({exc}). "
                f"Backed up to {backup}. Starting from empty settings.",
                file=sys.stderr,
            )
        except OSError as bak_exc:
            print(
                f"WARNING: {path} contains invalid JSON and backup failed: {bak_exc}. "
                "Starting from empty settings.",
                file=sys.stderr,
            )
        return {}, True


def write_settings_atomic(path: str, data: Dict[str, Any]) -> None:
    """Write *data* as indented JSON to *path* atomically via a .tmp file."""
    tmp_path = path + ".tmp"
    content = json.dumps(data, indent=2) + "\n"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge plugin hooks/env into Claude Code settings.json without clobbering user settings."
    )
    parser.add_argument("settings_path", help="Path to settings.json")
    parser.add_argument("--email", default="", help="CLAUDE_USER_EMAIL value to set")
    parser.add_argument("--org", default="", help="CLAUDE_ORG_NAME value to set")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove only plugin hook commands (inverse of merge)",
    )
    args = parser.parse_args(argv)

    settings_path = args.settings_path
    email = args.email.strip() if args.email else ""
    org = args.org.strip() if args.org else ""

    # Load (with corrupt-file recovery)
    data, was_fresh = load_settings(settings_path)
    if was_fresh and not os.path.exists(settings_path):
        print(f"No existing settings.json at {settings_path} — starting fresh.")

    # Merge or unmerge
    if args.uninstall:
        merged, log = unmerge_settings(data)
        print("Uninstall mode: removing plugin hooks from settings.json")
    else:
        merged, log = merge_settings(data, email=email or None, org=org or None)

    # Print summary
    for line in log:
        print(f"  {line}")

    # Write atomically
    write_settings_atomic(settings_path, merged)
    print(f"Wrote {settings_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
