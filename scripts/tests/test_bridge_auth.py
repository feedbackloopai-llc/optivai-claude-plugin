#!/usr/bin/env python3
"""fblai-g76hd — Pi bridge auth hardening tests.

Verifies:
  (a) envelope with user_id="victim" on a search op → search is invoked with
      OS-derived user_id, NOT "victim" (spy on search fn).
  (b) {"op":"admin_stats"} without OPEN_BRAIN_ALLOW_ADMIN env → returns an
      error object, admin_stats NOT called.
  (c) {"op":"admin_stats"} WITH OPEN_BRAIN_ALLOW_ADMIN=true → admin_stats IS
      called.

Tests do NOT require DATABASE_URL — the connection and all functions are
mocked.  They call _run_from_pi() directly via stdin monkeypatching.

Run: python3 -m pytest scripts/tests/test_bridge_auth.py -v
"""
import io
import json
import os
import sys
import unittest.mock as mock

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _run_pi_envelope(envelope: dict, monkeypatch, env_extras: dict = None):
    """Feed *envelope* through _run_from_pi() with a mocked DB connection.

    Returns (stdout_text, mock_conn).
    The caller patches individual functions as needed before calling this.
    """
    raw = json.dumps(envelope)
    captured_output = io.StringIO()

    env_extras = env_extras or {}

    fake_conn = mock.MagicMock()

    with mock.patch("open_brain._connect", return_value=fake_conn), \
         mock.patch("sys.stdin", io.StringIO(raw)), \
         mock.patch("sys.stdout", captured_output):
        # Patch env vars
        env_patch = {k: v for k, v in env_extras.items()}
        # Remove keys set to None (simulate absent)
        remove_keys = [k for k, v in env_patch.items() if v is None]
        set_keys = {k: v for k, v in env_patch.items() if v is not None}

        current_env = dict(os.environ)
        for k in remove_keys:
            current_env.pop(k, None)
        current_env.update(set_keys)

        with mock.patch.dict(os.environ, current_env, clear=True):
            open_brain._run_from_pi()

    return captured_output.getvalue(), fake_conn


# ─── Test (a): caller-supplied user_id is ignored ────────────────────────────


def test_caller_supplied_user_id_is_ignored(monkeypatch, capsys):
    """Envelope with user_id='victim' must NOT be forwarded to search()."""
    os_user = os.environ.get("USER") or os.environ.get("USERNAME") or os.environ.get("LOGNAME") or "unknown"
    expected_user_id = os_user.lower()

    captured_calls = []

    def spy_search(conn, query, user_id, **kwargs):
        captured_calls.append(user_id)
        return []  # empty results — no DB needed

    envelope = {
        "op": "search",
        "query": "test query",
        "user_id": "victim",  # attacker-supplied — must be dropped
    }

    with mock.patch("open_brain.search", side_effect=spy_search), \
         mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
         mock.patch("sys.stdin", io.StringIO(json.dumps(envelope))), \
         mock.patch("sys.stdout", io.StringIO()):
        open_brain._run_from_pi()

    assert len(captured_calls) == 1, "search() should have been called exactly once"
    actual_user_id = captured_calls[0]
    assert actual_user_id == expected_user_id, (
        f"search() was called with user_id={actual_user_id!r} but expected "
        f"OS-derived user_id={expected_user_id!r}; caller-supplied 'victim' must be ignored"
    )


# ─── Test (b): admin_stats blocked without env flag ──────────────────────────


def test_admin_stats_blocked_without_env(monkeypatch):
    """op=admin_stats without OPEN_BRAIN_ALLOW_ADMIN=true must return an error."""
    admin_stats_calls = []
    real_admin_stats = open_brain.admin_stats

    def spy_admin_stats(conn):
        admin_stats_calls.append(True)
        return real_admin_stats(conn)

    envelope = {"op": "admin_stats"}

    captured_output = io.StringIO()

    with mock.patch("open_brain.admin_stats", side_effect=spy_admin_stats), \
         mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
         mock.patch("sys.stdin", io.StringIO(json.dumps(envelope))), \
         mock.patch("sys.stdout", captured_output):
        # Ensure OPEN_BRAIN_ALLOW_ADMIN is absent
        env = {k: v for k, v in os.environ.items() if k != "OPEN_BRAIN_ALLOW_ADMIN"}
        with mock.patch.dict(os.environ, env, clear=True):
            open_brain._run_from_pi()

    assert len(admin_stats_calls) == 0, (
        "admin_stats() must NOT be called when OPEN_BRAIN_ALLOW_ADMIN is absent"
    )

    output = captured_output.getvalue()
    result = json.loads(output)
    assert "error" in result, (
        f"Expected an error key in the response, got: {result!r}"
    )
    assert "permission" in result["error"].lower() or "denied" in result["error"].lower() or "admin" in result["error"].lower(), (
        f"Error message should mention permission/access denial, got: {result['error']!r}"
    )


# ─── Test (c): admin_stats allowed WITH env flag ─────────────────────────────


def test_admin_stats_allowed_with_env_flag(monkeypatch):
    """op=admin_stats WITH OPEN_BRAIN_ALLOW_ADMIN=true must call admin_stats."""
    admin_stats_calls = []

    def spy_admin_stats(conn):
        admin_stats_calls.append(True)
        return {"status": "ok", "total_thoughts": 0}

    envelope = {"op": "admin_stats"}

    captured_output = io.StringIO()

    with mock.patch("open_brain.admin_stats", side_effect=spy_admin_stats), \
         mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
         mock.patch("sys.stdin", io.StringIO(json.dumps(envelope))), \
         mock.patch("sys.stdout", captured_output):
        env = dict(os.environ)
        env["OPEN_BRAIN_ALLOW_ADMIN"] = "true"
        with mock.patch.dict(os.environ, env, clear=True):
            open_brain._run_from_pi()

    assert len(admin_stats_calls) == 1, (
        "admin_stats() MUST be called when OPEN_BRAIN_ALLOW_ADMIN=true"
    )

    output = captured_output.getvalue()
    result = json.loads(output)
    assert "error" not in result, (
        f"Expected no error in response when admin flag is set, got: {result!r}"
    )
