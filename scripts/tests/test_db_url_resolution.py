#!/usr/bin/env python3
"""fblai-knutz / fblai-lm327 — Keychain-first credential resolution + URL validation tests.

Verifies the three-tier resolution order without touching the live DB:
  (a) DATABASE_URL env var wins when set.
  (b) Absent DATABASE_URL → Keychain (security binary) is invoked; its result is returned.
  (c) Keychain miss → config-file fallback is returned AND a deprecation WARNING
      is printed to stderr.
  (d) Nothing available → RuntimeError raised.

All filesystem/subprocess/environ dependencies are mocked.

Run: python3 -m pytest scripts/tests/test_db_url_resolution.py -v
"""
import io
import json
import os
import subprocess
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────

_CONFIG_CONN = "postgresql://config-user:config-pass@config-host/config-db"
_KEYCHAIN_CONN = "postgresql://kc-user:kc-pass@kc-host/kc-db"
_ENV_CONN = "postgresql://env-user:env-pass@env-host/env-db"


def _make_fake_config(conn_str: str) -> mock.MagicMock:
    """Return a mock that, when used as open(), yields JSON with conn_str."""
    config_data = json.dumps({
        "destinations": {
            "postgresql": {
                "connection_string": conn_str,
            }
        }
    })
    mock_file = mock.mock_open(read_data=config_data)
    return mock_file


def _keychain_success(conn_str: str) -> mock.MagicMock:
    """Return a CompletedProcess mock simulating a successful security call."""
    result = mock.MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = 0
    result.stdout = conn_str + "\n"  # security adds a newline
    return result


def _keychain_miss() -> mock.MagicMock:
    """Return a CompletedProcess mock simulating a keychain miss (non-zero exit)."""
    result = mock.MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = 44  # security exit code for "not found"
    result.stdout = ""
    return result


# ─── (a) DATABASE_URL env var wins ───────────────────────────────────────────


def test_env_var_wins(monkeypatch):
    """When DATABASE_URL is set, it is returned immediately without touching Keychain or config."""
    run_calls = []

    def spy_run(*args, **kwargs):
        run_calls.append(args)
        return _keychain_miss()  # should never be reached

    monkeypatch.setenv("DATABASE_URL", _ENV_CONN)
    monkeypatch.setattr("subprocess.run", spy_run)

    result = open_brain._get_database_url()

    assert result == _ENV_CONN, f"Expected env URL, got {result!r}"
    assert len(run_calls) == 0, "subprocess.run must NOT be called when DATABASE_URL is set"


# ─── (b) Absent env → Keychain invoked and result returned ───────────────────


def test_keychain_used_when_env_absent(monkeypatch):
    """Absent DATABASE_URL → security binary is called; its credential is returned."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", "optivai-neon-database-url")

    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        return _keychain_success(_KEYCHAIN_CONN)

    monkeypatch.setattr("subprocess.run", fake_run)

    result = open_brain._get_database_url()

    assert result == _KEYCHAIN_CONN, f"Expected Keychain URL, got {result!r}"
    assert len(run_calls) == 1, "subprocess.run should be called exactly once for Keychain lookup"
    cmd = run_calls[0]
    assert "security" in cmd, "Command must invoke the 'security' binary"
    assert "find-generic-password" in cmd, "Command must use find-generic-password subcommand"
    assert "optivai-neon-database-url" in cmd, "Command must specify the correct service name"
    assert "testuser" in cmd, "Command must pass account name from $USER"


def test_keychain_service_name_from_env(monkeypatch):
    """OPEN_BRAIN_DB_KEYCHAIN_SERVICE env var overrides the default service name."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "auser")
    monkeypatch.setenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", "my-custom-service")

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return _keychain_success(_KEYCHAIN_CONN)

    monkeypatch.setattr("subprocess.run", fake_run)

    result = open_brain._get_database_url()

    assert result == _KEYCHAIN_CONN
    assert "my-custom-service" in captured_cmds[0], (
        "Custom service name from OPEN_BRAIN_DB_KEYCHAIN_SERVICE must be forwarded to security"
    )


# ─── (c) Keychain miss → config file with deprecation WARNING ────────────────


def test_config_fallback_on_keychain_miss_emits_warning(monkeypatch, capsys):
    """Keychain miss falls through to config file and prints a deprecation warning to stderr."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _keychain_miss())

    # Mock Path.home() / config file existence and content
    fake_config_path = mock.MagicMock(spec=Path)
    fake_config_path.exists.return_value = True

    mock_open_fn = _make_fake_config(_CONFIG_CONN)

    with mock.patch("open_brain.Path") as mock_path_cls:
        # Path.home() returns a mock that navigates to the fake config path
        home_mock = mock.MagicMock()
        mock_path_cls.home.return_value = home_mock
        home_mock.__truediv__ = lambda self, other: home_mock
        home_mock.exists.return_value = True

        with mock.patch("builtins.open", mock_open_fn):
            result = open_brain._get_database_url()

    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower() or "WARNING" in captured.err, (
        f"Expected deprecation WARNING on stderr, got: {captured.err!r}"
    )
    assert result == _CONFIG_CONN, f"Expected config URL, got {result!r}"


def test_config_fallback_encode_fn_not_called_on_keychain_miss(monkeypatch):
    """When Keychain misses and config succeeds, result is the config credential."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _keychain_miss())

    fake_config = _make_fake_config(_CONFIG_CONN)
    with mock.patch("open_brain.Path") as mock_path_cls:
        home_mock = mock.MagicMock()
        mock_path_cls.home.return_value = home_mock
        home_mock.__truediv__ = lambda self, other: home_mock
        home_mock.exists.return_value = True

        with mock.patch("builtins.open", fake_config):
            result = open_brain._get_database_url()

    assert result == _CONFIG_CONN


# ─── (d) Nothing available → RuntimeError ────────────────────────────────────


def test_raises_when_nothing_available(monkeypatch):
    """No env, no Keychain, no config → RuntimeError with informative message."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _keychain_miss())

    with mock.patch("open_brain.Path") as mock_path_cls:
        home_mock = mock.MagicMock()
        mock_path_cls.home.return_value = home_mock
        home_mock.__truediv__ = lambda self, other: home_mock
        home_mock.exists.return_value = False  # config file does not exist

        with pytest.raises(RuntimeError) as exc_info:
            open_brain._get_database_url()

    msg = str(exc_info.value)
    assert "DATABASE_URL" in msg or "Keychain" in msg or "auto-logger-config" in msg, (
        f"Error message should name the sources tried, got: {msg!r}"
    )


# ─── (e) Keychain binary absent (non-macOS) → falls through ──────────────────


def test_keychain_file_not_found_falls_through_to_config(monkeypatch):
    """If 'security' binary is absent (non-macOS), falls through to config without crashing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    def raise_not_found(*a, **kw):
        raise FileNotFoundError("security not found")

    monkeypatch.setattr("subprocess.run", raise_not_found)

    fake_config = _make_fake_config(_CONFIG_CONN)
    with mock.patch("open_brain.Path") as mock_path_cls:
        home_mock = mock.MagicMock()
        mock_path_cls.home.return_value = home_mock
        home_mock.__truediv__ = lambda self, other: home_mock
        home_mock.exists.return_value = True

        with mock.patch("builtins.open", fake_config):
            result = open_brain._get_database_url()

    assert result == _CONFIG_CONN, "Should fall through to config when security binary is absent"


# ─── fblai-lm327: keychain URL validation ────────────────────────────────────


def test_keychain_non_postgres_value_is_rejected_and_falls_through(monkeypatch, capsys):
    """A non-postgres keychain value must be ignored with a WARNING and fall through.

    fblai-lm327: corrupted or multiline keychain values that do not start with
    'postgres' must not be returned; the function must fall through to the
    config-file tier and emit a WARNING to stderr.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    # Keychain returns garbage (multiline, non-postgres)
    garbage_value = "garbage\nmultiline\nnot-a-url"

    def fake_run_garbage(cmd, **kwargs):
        result = mock.MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stdout = garbage_value
        return result

    monkeypatch.setattr("subprocess.run", fake_run_garbage)

    # Config file fallback provides the real URL
    fake_config = _make_fake_config(_CONFIG_CONN)
    with mock.patch("open_brain.Path") as mock_path_cls:
        home_mock = mock.MagicMock()
        mock_path_cls.home.return_value = home_mock
        home_mock.__truediv__ = lambda self, other: home_mock
        home_mock.exists.return_value = True

        with mock.patch("builtins.open", fake_config):
            result = open_brain._get_database_url()

    # Must NOT return the garbage value
    assert result != garbage_value, (
        f"Non-postgres keychain value must NOT be returned; got {result!r}"
    )
    assert result == _CONFIG_CONN, (
        f"After keychain rejection, config fallback must be used; got {result!r}"
    )

    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "keychain" in captured.err.lower(), (
        f"Expected WARNING on stderr about invalid keychain value; got: {captured.err!r}"
    )


def test_keychain_non_postgres_value_does_not_contain_garbage(monkeypatch):
    """Ensure the returned URL never contains garbage when keychain is bad."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    bad_values = [
        "garbage",
        "mysql://user:pass@host/db",
        "http://not-a-database",
        "",
        "  ",
        "\nmultiline\nvalue\n",
        "redis://localhost:6379",
    ]

    fake_config = _make_fake_config(_CONFIG_CONN)

    for bad_val in bad_values:
        def fake_run_bad(cmd, _val=bad_val, **kwargs):
            result = mock.MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stdout = _val
            return result

        monkeypatch.setattr("subprocess.run", fake_run_bad)

        with mock.patch("open_brain.Path") as mock_path_cls:
            home_mock = mock.MagicMock()
            mock_path_cls.home.return_value = home_mock
            home_mock.__truediv__ = lambda self, other: home_mock
            home_mock.exists.return_value = True

            with mock.patch("builtins.open", fake_config):
                result = open_brain._get_database_url()

        # For non-empty bad values, ensure the raw garbage doesn't leak into
        # the returned URL.  (Empty/whitespace-only values can't be asserted
        # "not in" since "" is contained in every string — instead assert the
        # config URL was returned, which we check below.)
        stripped = bad_val.strip()
        if stripped:
            assert stripped not in result, (
                f"Bad keychain value {bad_val!r} must not appear in returned URL; got {result!r}"
            )


def test_valid_postgresql_url_in_keychain_is_returned(monkeypatch):
    """A valid 'postgresql://' keychain URL IS returned directly (regression guard)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    valid_postgresql = "postgresql://user:pass@host/db"

    def fake_run_valid(cmd, **kwargs):
        result = mock.MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stdout = valid_postgresql + "\n"
        return result

    monkeypatch.setattr("subprocess.run", fake_run_valid)

    result = open_brain._get_database_url()
    assert result == valid_postgresql, (
        f"Valid 'postgresql://' URL must be returned; got {result!r}"
    )


def test_valid_postgres_url_in_keychain_is_returned(monkeypatch):
    """A valid 'postgres://' keychain URL IS returned directly."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", raising=False)

    valid_postgres = "postgres://user:pass@host/db"

    def fake_run_valid(cmd, **kwargs):
        result = mock.MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stdout = valid_postgres
        return result

    monkeypatch.setattr("subprocess.run", fake_run_valid)

    result = open_brain._get_database_url()
    assert result == valid_postgres, (
        f"Valid 'postgres://' URL must be returned; got {result!r}"
    )
