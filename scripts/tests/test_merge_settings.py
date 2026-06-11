"""
Tests for scripts/merge_settings.py

TDD coverage:
  1. Merging into {} produces all required hooks + env (fresh install).
  2. Merging into a settings.json that ALREADY has the hooks is a NO-OP
     (idempotent — no duplicate commands; run merge twice, assert equal).
  3. A user's existing UNRELATED hook is PRESERVED after merge.
  4. Existing top-level keys (model, permissions, enabledPlugins, a made-up
     "customKey") are PRESERVED.
  5. Existing env keys the user set (e.g. a custom env var, or their own
     CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="0") are not clobbered
     (only added if missing; email/org overwrite if provided).
  6. Invalid/corrupt JSON input → backed up + fresh start, no crash.
  7. The atomic write produces valid JSON re-loadable.
  8. Uninstall mode removes ONLY the plugin's known commands, leaving
     unrelated hooks intact.
  9. Uninstall mode on a settings.json that never had plugin hooks is a no-op.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

# Make scripts/ importable when running from repo root
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from merge_settings import (  # noqa: E402
    PLUGIN_HOOKS,
    load_settings,
    main,
    merge_settings,
    unmerge_settings,
    write_settings_atomic,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

REALISTIC_EXISTING = {
    "model": "claude-opus-4-7",
    "effortLevel": "high",
    "enabledPlugins": ["frontend-design"],
    "permissions": {
        "allow": ["Bash(git:*)", "Bash(npm:*)"],
        "deny": [],
    },
    "agentPushNotifEnabled": True,
    "skipDangerousModePermissionPrompt": False,
    "statusLine": "compact",
    "customKey": "user-value",
}

ALL_PLUGIN_COMMANDS = [
    cmd
    for _matcher, cmds in PLUGIN_HOOKS.values()
    for cmd in cmds
]


def _all_hook_commands(settings: Dict[str, Any]) -> list:
    """Flatten all command strings from settings['hooks']."""
    found = []
    for event_groups in settings.get("hooks", {}).values():
        for group in event_groups:
            for h in group.get("hooks", []):
                found.append(h.get("command"))
    return found


def _command_present_in_event(settings: Dict[str, Any], event: str, cmd: str) -> bool:
    for group in settings.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            if h.get("command") == cmd:
                return True
    return False


# ---------------------------------------------------------------------------
# 1. Fresh install — {} → all required hooks + env
# ---------------------------------------------------------------------------

class TestFreshInstall:
    def test_all_env_keys_present(self):
        merged, _ = merge_settings({})
        assert merged["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"

    def test_all_hook_events_present(self):
        merged, _ = merge_settings({})
        for event in PLUGIN_HOOKS:
            assert event in merged["hooks"], f"Missing event: {event}"

    def test_all_commands_present(self):
        merged, _ = merge_settings({})
        found = _all_hook_commands(merged)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert cmd in found, f"Missing command: {cmd}"

    def test_session_start_has_no_matcher(self):
        merged, _ = merge_settings({})
        groups = merged["hooks"]["SessionStart"]
        for group in groups:
            assert "matcher" not in group

    def test_pre_tool_use_has_star_matcher(self):
        merged, _ = merge_settings({})
        groups = merged["hooks"]["PreToolUse"]
        assert any(g.get("matcher") == "*" for g in groups)

    def test_user_prompt_submit_has_star_matcher(self):
        merged, _ = merge_settings({})
        groups = merged["hooks"]["UserPromptSubmit"]
        assert any(g.get("matcher") == "*" for g in groups)

    def test_stop_has_no_matcher(self):
        merged, _ = merge_settings({})
        groups = merged["hooks"]["Stop"]
        for group in groups:
            assert "matcher" not in group

    def test_email_added_when_provided(self):
        merged, _ = merge_settings({}, email="test@example.com")
        assert merged["env"]["CLAUDE_USER_EMAIL"] == "test@example.com"

    def test_org_added_when_provided(self):
        merged, _ = merge_settings({}, org="FBLAI")
        assert merged["env"]["CLAUDE_ORG_NAME"] == "FBLAI"

    def test_email_not_set_when_not_provided(self):
        merged, _ = merge_settings({})
        assert "CLAUDE_USER_EMAIL" not in merged["env"]

    def test_org_not_set_when_not_provided(self):
        merged, _ = merge_settings({})
        assert "CLAUDE_ORG_NAME" not in merged["env"]


# ---------------------------------------------------------------------------
# 2. Idempotency — merge twice == equal
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_double_merge_equals_single_merge(self):
        first, _ = merge_settings({})
        second, _ = merge_settings(first)
        assert first == second

    def test_no_duplicate_commands_after_double_merge(self):
        first, _ = merge_settings({})
        second, _ = merge_settings(first)
        found = _all_hook_commands(second)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert found.count(cmd) == 1, (
                f"Command '{cmd}' appears {found.count(cmd)} times after double merge"
            )

    def test_triple_merge_still_no_duplicates(self):
        merged, _ = merge_settings({})
        for _ in range(2):
            merged, _ = merge_settings(merged)
        found = _all_hook_commands(merged)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert found.count(cmd) == 1

    def test_idempotent_on_existing_full_settings(self):
        """Start from a realistic settings with hooks already wired."""
        base, _ = merge_settings(REALISTIC_EXISTING)
        again, _ = merge_settings(base)
        assert base == again

    def test_log_says_already_present_on_second_merge(self):
        first, _ = merge_settings({})
        _second, log = merge_settings(first)
        already_present_lines = [l for l in log if "already present" in l]
        # All hook commands should be reported as already present
        assert len(already_present_lines) == len(ALL_PLUGIN_COMMANDS)


# ---------------------------------------------------------------------------
# 3. User's unrelated hooks are preserved
# ---------------------------------------------------------------------------

class TestPreservesUserHooks:
    def test_custom_pre_tool_use_hook_preserved(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": "python3 ~/my-custom-hook.py"}
                        ],
                    }
                ]
            }
        }
        merged, _ = merge_settings(existing)
        assert _command_present_in_event(merged, "PreToolUse", "python3 ~/my-custom-hook.py")

    def test_notification_hook_preserved(self):
        existing = {
            "hooks": {
                "Notification": [
                    {
                        "hooks": [
                            {"type": "command", "command": "bash ~/notify.sh"}
                        ]
                    }
                ]
            }
        }
        merged, _ = merge_settings(existing)
        assert _command_present_in_event(merged, "Notification", "bash ~/notify.sh")

    def test_custom_stop_hook_preserved(self):
        existing = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "python3 ~/my-stop.py"}
                        ]
                    }
                ]
            }
        }
        merged, _ = merge_settings(existing)
        assert _command_present_in_event(merged, "Stop", "python3 ~/my-stop.py")

    def test_user_hook_count_not_reduced(self):
        """The number of unique commands in the user's existing hooks never decreases."""
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": "bash ~/user-check.sh"}
                        ],
                    }
                ]
            }
        }
        before_user_cmds = _all_hook_commands(existing)
        merged, _ = merge_settings(existing)
        after_cmds = _all_hook_commands(merged)
        for cmd in before_user_cmds:
            assert cmd in after_cmds, f"User command '{cmd}' was lost after merge"


# ---------------------------------------------------------------------------
# 4. Top-level keys preserved
# ---------------------------------------------------------------------------

class TestPreservesTopLevelKeys:
    def test_model_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["model"] == "claude-opus-4-7"

    def test_effort_level_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["effortLevel"] == "high"

    def test_enabled_plugins_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["enabledPlugins"] == ["frontend-design"]

    def test_permissions_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["permissions"]["allow"] == ["Bash(git:*)", "Bash(npm:*)"]

    def test_agent_push_notif_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["agentPushNotifEnabled"] is True

    def test_skip_dangerous_mode_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["skipDangerousModePermissionPrompt"] is False

    def test_status_line_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["statusLine"] == "compact"

    def test_unknown_custom_key_preserved(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        assert merged["customKey"] == "user-value"

    def test_no_new_top_level_keys_except_env_and_hooks(self):
        """Only 'env' and 'hooks' may be added; nothing else."""
        existing = {"model": "opus", "customKey": "val"}
        merged, _ = merge_settings(existing)
        allowed_new = {"env", "hooks"}
        new_keys = set(merged.keys()) - set(existing.keys())
        assert new_keys <= allowed_new


# ---------------------------------------------------------------------------
# 5. Env keys not clobbered (only added if missing)
# ---------------------------------------------------------------------------

class TestEnvPreservation:
    def test_user_agent_teams_value_not_overwritten(self):
        """If user already set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS='0', keep it."""
        existing = {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0"}}
        merged, _ = merge_settings(existing)
        assert merged["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "0"

    def test_user_custom_env_var_preserved(self):
        existing = {"env": {"MY_CUSTOM_VAR": "hello"}}
        merged, _ = merge_settings(existing)
        assert merged["env"]["MY_CUSTOM_VAR"] == "hello"

    def test_email_overwrites_existing_email(self):
        """email/org are always overwritten when provided (user just typed them)."""
        existing = {"env": {"CLAUDE_USER_EMAIL": "old@example.com"}}
        merged, _ = merge_settings(existing, email="new@example.com")
        assert merged["env"]["CLAUDE_USER_EMAIL"] == "new@example.com"

    def test_org_overwrites_existing_org(self):
        existing = {"env": {"CLAUDE_ORG_NAME": "OldOrg"}}
        merged, _ = merge_settings(existing, org="NewOrg")
        assert merged["env"]["CLAUDE_ORG_NAME"] == "NewOrg"

    def test_empty_email_does_not_set_key(self):
        existing = {}
        merged, _ = merge_settings(existing, email="")
        assert "CLAUDE_USER_EMAIL" not in merged["env"]

    def test_empty_org_does_not_set_key(self):
        existing = {}
        merged, _ = merge_settings(existing, org="")
        assert "CLAUDE_ORG_NAME" not in merged["env"]

    def test_multiple_existing_env_vars_all_preserved(self):
        existing = {
            "env": {
                "CUSTOM_A": "a",
                "CUSTOM_B": "b",
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            }
        }
        merged, _ = merge_settings(existing)
        assert merged["env"]["CUSTOM_A"] == "a"
        assert merged["env"]["CUSTOM_B"] == "b"
        assert merged["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"


# ---------------------------------------------------------------------------
# 6. Corrupt / invalid JSON — backed up + fresh start, no crash
# ---------------------------------------------------------------------------

class TestCorruptJsonHandling:
    def test_invalid_json_does_not_crash(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("{not valid json!!!}")
        data, was_fresh = load_settings(settings_path)
        assert was_fresh is True
        assert data == {}

    def test_invalid_json_creates_backup(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("{broken")
        load_settings(settings_path)
        backups = list(tmp_path.glob("settings.json.corrupt-*"))
        assert len(backups) == 1

    def test_backup_contains_original_content(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        bad_content = "{broken json}"
        with open(settings_path, "w") as f:
            f.write(bad_content)
        load_settings(settings_path)
        backups = list(tmp_path.glob("settings.json.corrupt-*"))
        assert backups[0].read_text() == bad_content

    def test_absent_file_returns_fresh(self, tmp_path):
        data, was_fresh = load_settings(str(tmp_path / "nonexistent.json"))
        assert was_fresh is True
        assert data == {}

    def test_empty_file_returns_fresh(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("")
        data, was_fresh = load_settings(settings_path)
        assert was_fresh is True
        assert data == {}

    def test_whitespace_only_file_returns_fresh(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("   \n\t  \n")
        data, was_fresh = load_settings(settings_path)
        assert was_fresh is True
        assert data == {}

    def test_non_object_json_returns_fresh(self, tmp_path):
        """Top-level JSON array is also invalid for settings.json."""
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("[1, 2, 3]")
        data, was_fresh = load_settings(settings_path)
        assert was_fresh is True
        assert data == {}

    def test_full_pipeline_with_corrupt_input(self, tmp_path):
        """End-to-end: corrupt file → main() produces valid settings.json."""
        settings_path = str(tmp_path / "settings.json")
        with open(settings_path, "w") as f:
            f.write("{completely broken}")
        rc = main([settings_path])
        assert rc == 0
        with open(settings_path) as f:
            data = json.load(f)
        assert "hooks" in data
        assert "env" in data


# ---------------------------------------------------------------------------
# 7. Atomic write produces valid JSON
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_written_file_is_valid_json(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        merged, _ = merge_settings({})
        write_settings_atomic(settings_path, merged)
        with open(settings_path) as f:
            reloaded = json.load(f)
        assert "hooks" in reloaded
        assert "env" in reloaded

    def test_written_file_ends_with_newline(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        merged, _ = merge_settings({})
        write_settings_atomic(settings_path, merged)
        content = Path(settings_path).read_text()
        assert content.endswith("\n")

    def test_written_file_is_indented(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        merged, _ = merge_settings({})
        write_settings_atomic(settings_path, merged)
        content = Path(settings_path).read_text()
        # indent=2 means lines start with spaces
        assert "  " in content

    def test_reload_after_main(self, tmp_path):
        """Full round-trip: main() writes, load_settings() reads back clean."""
        settings_path = str(tmp_path / "settings.json")
        rc = main([settings_path, "--email", "test@test.com", "--org", "Acme"])
        assert rc == 0
        data, was_fresh = load_settings(settings_path)
        assert was_fresh is False
        assert data["env"]["CLAUDE_USER_EMAIL"] == "test@test.com"
        assert data["env"]["CLAUDE_ORG_NAME"] == "Acme"

    def test_no_tmp_file_left_after_write(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        merged, _ = merge_settings({})
        write_settings_atomic(settings_path, merged)
        assert not os.path.exists(settings_path + ".tmp")


# ---------------------------------------------------------------------------
# 8. Uninstall mode — remove only plugin commands, leave others
# ---------------------------------------------------------------------------

class TestUninstallMode:
    def test_uninstall_removes_plugin_commands(self):
        installed, _ = merge_settings({})
        cleaned, log = unmerge_settings(installed)
        found = _all_hook_commands(cleaned)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert cmd not in found, f"Plugin command '{cmd}' still present after uninstall"

    def test_uninstall_preserves_user_pre_tool_use(self):
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": "python3 ~/user-hook.py"}
                        ],
                    }
                ]
            }
        }
        installed, _ = merge_settings(existing)
        cleaned, _ = unmerge_settings(installed)
        assert _command_present_in_event(cleaned, "PreToolUse", "python3 ~/user-hook.py")

    def test_uninstall_preserves_top_level_keys(self):
        installed, _ = merge_settings(REALISTIC_EXISTING)
        cleaned, _ = unmerge_settings(installed)
        assert cleaned["model"] == "claude-opus-4-7"
        assert cleaned["effortLevel"] == "high"
        assert cleaned["customKey"] == "user-value"

    def test_uninstall_preserves_env(self):
        installed, _ = merge_settings(REALISTIC_EXISTING, email="chris@test.com")
        cleaned, _ = unmerge_settings(installed)
        # Uninstall does not touch env
        assert cleaned["env"]["CLAUDE_USER_EMAIL"] == "chris@test.com"

    def test_uninstall_noop_when_no_plugin_hooks(self):
        """Uninstalling on a clean settings is a no-op (no crash, no data loss)."""
        cleaned, log = unmerge_settings(REALISTIC_EXISTING)
        # Should not have removed any user hooks
        for key, val in REALISTIC_EXISTING.items():
            if key != "hooks":  # REALISTIC_EXISTING has no hooks
                assert cleaned.get(key) == val

    def test_uninstall_via_cli_flag(self, tmp_path):
        settings_path = str(tmp_path / "settings.json")
        # Install first
        main([settings_path])
        # Now uninstall
        rc = main([settings_path, "--uninstall"])
        assert rc == 0
        data, _ = load_settings(settings_path)
        found = _all_hook_commands(data)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert cmd not in found

    def test_uninstall_and_reinstall_is_idempotent(self):
        """install → uninstall → install should equal a single install."""
        first, _ = merge_settings({})
        uninstalled, _ = unmerge_settings(first)
        reinstalled, _ = merge_settings(uninstalled)
        # All plugin commands should be present
        found = _all_hook_commands(reinstalled)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert cmd in found


# ---------------------------------------------------------------------------
# 9. Fixture-preservation — realistic settings.json survives merge
# ---------------------------------------------------------------------------

class TestRealisticFixturePreservation:
    def test_realistic_settings_all_prefs_survive(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        preserved_keys = [
            "model", "effortLevel", "enabledPlugins",
            "permissions", "agentPushNotifEnabled",
            "skipDangerousModePermissionPrompt", "statusLine", "customKey",
        ]
        for key in preserved_keys:
            assert key in merged, f"Key '{key}' was dropped"
            assert merged[key] == REALISTIC_EXISTING[key], (
                f"Key '{key}' value changed: {merged[key]!r} != {REALISTIC_EXISTING[key]!r}"
            )

    def test_realistic_settings_plugin_hooks_added(self):
        merged, _ = merge_settings(REALISTIC_EXISTING)
        found = _all_hook_commands(merged)
        for cmd in ALL_PLUGIN_COMMANDS:
            assert cmd in found

    def test_realistic_settings_with_existing_hooks_and_prefs(self):
        """User has both prefs AND some existing hooks + the plugin re-installs."""
        existing = dict(REALISTIC_EXISTING)
        existing["hooks"] = {
            "Notification": [
                {"hooks": [{"type": "command", "command": "bash ~/notify.sh"}]}
            ]
        }
        merged, _ = merge_settings(existing)
        # Prefs intact
        assert merged["model"] == "claude-opus-4-7"
        # User's notification hook intact
        assert _command_present_in_event(merged, "Notification", "bash ~/notify.sh")
        # Plugin hooks added
        for cmd in ALL_PLUGIN_COMMANDS:
            found = _all_hook_commands(merged)
            assert cmd in found

    def test_second_install_with_prefs_is_idempotent(self):
        """Simulates running install.sh twice on a configured machine."""
        first, _ = merge_settings(REALISTIC_EXISTING)
        second, _ = merge_settings(first)
        assert first == second
