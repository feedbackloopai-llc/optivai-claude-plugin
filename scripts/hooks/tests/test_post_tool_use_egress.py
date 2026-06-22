"""Tests for scripts/hooks/post_tool_use.py — L6b egress redaction hook.

TDD coverage:
  (a) PostToolUse envelope whose tool_result contains a fake AWS key or
      Anthropic key → the redacted output written to the activity log does
      NOT contain the secret.
  (b) Malformed JSON stdin → fail-open (no exception raised, silent exit).
  (c) Missing tool_result field → fail-open (no exception, silent exit).
  (d) merge_settings PLUGIN_HOOKS constant includes a PostToolUse entry
      with the canonical command; a merge starting from {} produces a
      PostToolUse block in the output.

Protocol note: the hook writes its redacted output to the activity log; it
does NOT emit anything to stdout (stdout is reserved for Claude Code hook
directives). Tests verify the log-write path, not stdout.

Run from repo root: cd scripts && python3 -m pytest hooks/tests/test_post_tool_use_egress.py -v
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

HOOKS_DIR = Path(__file__).resolve().parent.parent          # scripts/hooks/
SCRIPTS_DIR = HOOKS_DIR.parent                               # scripts/
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import post_tool_use as ptu  # noqa: E402

# ── Fake secret corpus ────────────────────────────────────────────────────────

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_ANTHROPIC_KEY = (
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def _make_envelope(tool_name: str = "Bash", tool_result: object = "") -> dict:
    """Build a minimal PostToolUse stdin envelope."""
    return {
        "tool_name": tool_name,
        "tool_input": {"command": "cat /etc/config"},
        "tool_result": tool_result,
    }


# ── Helpers to drive main() in-process ────────────────────────────────────────

def _run_main_with_stdin(envelope: object) -> tuple:
    """Drive post_tool_use.main() with *envelope* as stdin JSON.

    Returns (stdout_captured, log_records) where log_records is a list of
    dicts written to the activity log during the call.

    We patch _write_output_log so we can inspect the redacted output without
    touching the real filesystem.
    """
    raw = json.dumps(envelope)
    log_calls = []

    def _capture_log(tool_name: str, redacted_output: str) -> None:
        log_calls.append({"tool_name": tool_name, "redacted_output": redacted_output})

    stdout_buf = io.StringIO()

    with patch.object(ptu, "_write_output_log", side_effect=_capture_log):
        with patch("sys.stdin", io.StringIO(raw)):
            with redirect_stdout(stdout_buf):
                ptu.main()

    return stdout_buf.getvalue(), log_calls


def _run_main_with_raw_stdin(raw: str) -> tuple:
    """Like _run_main_with_stdin but accepts raw string (for bad-JSON tests)."""
    log_calls = []

    def _capture_log(tool_name: str, redacted_output: str) -> None:
        log_calls.append({"tool_name": tool_name, "redacted_output": redacted_output})

    stdout_buf = io.StringIO()

    with patch.object(ptu, "_write_output_log", side_effect=_capture_log):
        with patch("sys.stdin", io.StringIO(raw)):
            with redirect_stdout(stdout_buf):
                ptu.main()

    return stdout_buf.getvalue(), log_calls


# ── (a) Secrets are redacted in the activity log ─────────────────────────────

class TestSecretRedaction:
    """Tool results containing secrets are redacted before log write."""

    def test_aws_key_not_in_log(self):
        """AWS access key in tool_result must not appear in the log record."""
        envelope = _make_envelope(
            tool_name="Bash",
            tool_result=f"The key is {FAKE_AWS_KEY} in the output",
        )
        _, log_calls = _run_main_with_stdin(envelope)

        assert len(log_calls) == 1, f"Expected 1 log call, got {len(log_calls)}"
        redacted = log_calls[0]["redacted_output"]
        assert FAKE_AWS_KEY not in redacted, (
            f"AWS key found unredacted in log output: {redacted!r}"
        )

    def test_anthropic_key_not_in_log(self):
        """Anthropic API key in tool_result must not appear in the log record."""
        envelope = _make_envelope(
            tool_name="Read",
            tool_result=f"export ANT_KEY={FAKE_ANTHROPIC_KEY}",
        )
        _, log_calls = _run_main_with_stdin(envelope)

        assert len(log_calls) == 1, f"Expected 1 log call, got {len(log_calls)}"
        redacted = log_calls[0]["redacted_output"]
        assert FAKE_ANTHROPIC_KEY not in redacted, (
            f"Anthropic key found unredacted in log output: {redacted!r}"
        )

    def test_clean_output_passes_through_unchanged(self):
        """Clean tool output (no secrets) should pass through unmodified."""
        clean = "total 3 files found in /tmp"
        envelope = _make_envelope(tool_name="Bash", tool_result=clean)
        _, log_calls = _run_main_with_stdin(envelope)

        assert len(log_calls) == 1
        assert log_calls[0]["redacted_output"] == clean, (
            f"Clean output was mangled: {log_calls[0]['redacted_output']!r}"
        )

    def test_tool_name_preserved_in_log(self):
        """The tool_name from the envelope must appear in the log record."""
        envelope = _make_envelope(tool_name="Write", tool_result="ok")
        _, log_calls = _run_main_with_stdin(envelope)

        assert len(log_calls) == 1
        assert log_calls[0]["tool_name"] == "Write"

    def test_no_stdout_emitted(self):
        """The hook must not emit anything to stdout (Claude Code reads stdout)."""
        envelope = _make_envelope(
            tool_name="Bash",
            tool_result=f"key={FAKE_AWS_KEY}",
        )
        stdout, _ = _run_main_with_stdin(envelope)
        assert stdout == "", f"Hook emitted unexpected stdout: {stdout!r}"


# ── (b) Malformed JSON stdin → fail-open ─────────────────────────────────────

class TestMalformedJsonFailOpen:
    """Bad JSON on stdin must not raise; hook exits silently."""

    def test_completely_invalid_json(self):
        """Garbage input does not raise."""
        stdout, log_calls = _run_main_with_raw_stdin("not json at all {{{")
        assert stdout == ""
        assert log_calls == [], "No log write should occur on bad JSON"

    def test_truncated_json(self):
        """Truncated JSON does not raise."""
        stdout, log_calls = _run_main_with_raw_stdin('{"tool_name": "Bash", "tool_result"')
        assert stdout == ""
        assert log_calls == []

    def test_empty_stdin(self):
        """Empty stdin does not raise."""
        stdout, log_calls = _run_main_with_raw_stdin("")
        assert stdout == ""
        assert log_calls == []

    def test_whitespace_only_stdin(self):
        """Whitespace-only stdin does not raise."""
        stdout, log_calls = _run_main_with_raw_stdin("   \n\t  ")
        assert stdout == ""
        assert log_calls == []

    def test_json_array_not_object(self):
        """A JSON array (not object) does not raise and emits nothing."""
        stdout, log_calls = _run_main_with_raw_stdin("[1, 2, 3]")
        assert stdout == ""
        assert log_calls == []


# ── (c) Missing tool_result field → fail-open ────────────────────────────────

class TestMissingOutputFieldFailOpen:
    """Envelopes without tool_result/tool_response must not crash."""

    def test_no_tool_result_field(self):
        """Envelope with no output field exits silently without a log write."""
        envelope = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        stdout, log_calls = _run_main_with_stdin(envelope)
        assert stdout == ""
        assert log_calls == [], "No log write should occur when tool_result is absent"

    def test_null_tool_result(self):
        """Envelope with null tool_result exits silently."""
        envelope = {"tool_name": "Read", "tool_result": None}
        stdout, log_calls = _run_main_with_stdin(envelope)
        assert stdout == ""
        assert log_calls == []

    def test_empty_string_tool_result(self):
        """Empty string tool_result exits silently (nothing to redact)."""
        envelope = {"tool_name": "Bash", "tool_result": ""}
        stdout, log_calls = _run_main_with_stdin(envelope)
        assert stdout == ""
        assert log_calls == []

    def test_tool_response_fallback(self):
        """Falls back to tool_response key if tool_result is absent."""
        clean = "output via tool_response field"
        envelope = {
            "tool_name": "Bash",
            "tool_input": {},
            "tool_response": clean,
        }
        stdout, log_calls = _run_main_with_stdin(envelope)
        assert stdout == ""
        assert len(log_calls) == 1, "Should log when tool_response present"
        assert log_calls[0]["redacted_output"] == clean


# ── (d) merge_settings PLUGIN_HOOKS includes PostToolUse ─────────────────────

class TestMergeSettingsPostToolUse:
    """merge_settings wires the PostToolUse hook into settings.json."""

    def test_plugin_hooks_has_posttooluse(self):
        """PLUGIN_HOOKS constant must contain a PostToolUse entry."""
        from merge_settings import PLUGIN_HOOKS  # noqa: E402

        assert "PostToolUse" in PLUGIN_HOOKS, (
            "PLUGIN_HOOKS is missing PostToolUse; hook cannot be installed automatically"
        )

    def test_posttooluse_command_is_canonical(self):
        """The PostToolUse command must point to post_tool_use.py via tilde path."""
        from merge_settings import PLUGIN_HOOKS

        matcher, commands = PLUGIN_HOOKS["PostToolUse"]
        assert matcher == "*", f"PostToolUse matcher should be '*', got {matcher!r}"
        assert len(commands) >= 1, "PostToolUse must have at least one command"
        canonical = "python3 ~/.claude/hooks/post_tool_use.py"
        assert canonical in commands, (
            f"Canonical PostToolUse command {canonical!r} not found; got {commands}"
        )

    def test_merge_produces_posttooluse_block(self):
        """A fresh merge from {} produces a PostToolUse hooks block."""
        from merge_settings import merge_settings

        merged, log = merge_settings({})
        hooks = merged.get("hooks", {})
        assert "PostToolUse" in hooks, (
            f"PostToolUse block missing from merged hooks. Keys: {list(hooks.keys())}"
        )

    def test_merge_posttooluse_command_present(self):
        """The canonical command string appears in the merged PostToolUse block."""
        from merge_settings import merge_settings

        merged, _ = merge_settings({})
        groups = merged["hooks"]["PostToolUse"]
        commands_found = [
            h.get("command")
            for group in groups
            for h in group.get("hooks", [])
        ]
        assert "python3 ~/.claude/hooks/post_tool_use.py" in commands_found, (
            f"Canonical command not in merged PostToolUse. Found: {commands_found}"
        )

    def test_merge_is_idempotent_for_posttooluse(self):
        """Running merge twice does not duplicate the PostToolUse command."""
        from merge_settings import merge_settings

        merged_once, _ = merge_settings({})
        merged_twice, _ = merge_settings(merged_once)

        commands_once = [
            h.get("command")
            for group in merged_once["hooks"]["PostToolUse"]
            for h in group.get("hooks", [])
        ]
        commands_twice = [
            h.get("command")
            for group in merged_twice["hooks"]["PostToolUse"]
            for h in group.get("hooks", [])
        ]
        canonical = "python3 ~/.claude/hooks/post_tool_use.py"
        assert commands_once.count(canonical) == 1, (
            f"After first merge: {commands_once.count(canonical)} copies of canonical command"
        )
        assert commands_twice.count(canonical) == 1, (
            f"After second merge (idempotent check): {commands_twice.count(canonical)} copies"
        )

    def test_legacy_variants_registered_for_posttooluse(self):
        """LEGACY_HOOK_VARIANTS contains PostToolUse for upgrade migration."""
        from merge_settings import LEGACY_HOOK_VARIANTS

        assert "PostToolUse" in LEGACY_HOOK_VARIANTS, (
            "LEGACY_HOOK_VARIANTS must include PostToolUse for clean upgrade migration"
        )

    def test_uninstall_removes_posttooluse(self):
        """Uninstall mode removes the PostToolUse command."""
        from merge_settings import merge_settings, unmerge_settings

        merged, _ = merge_settings({})
        unmerged, _ = unmerge_settings(merged)

        # PostToolUse block should be gone entirely after uninstall.
        assert "PostToolUse" not in unmerged.get("hooks", {}), (
            "PostToolUse block should be removed by uninstall"
        )
