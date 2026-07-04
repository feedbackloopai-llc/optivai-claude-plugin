#!/usr/bin/env python3
"""Finding 10 (review fix) — pg_sync None-writer guard regression test.

SyncService.__init__ leaves self.writer = None when PostgreSQL sync is
enabled in config but no connection string resolves (no config
connection_string and no DATABASE_URL env var). Previously, run_once()
would then call self.writer.write_batch(...) directly, raising an
AttributeError ("NoneType has no attribute 'write_batch'") that got
caught by the generic retry `except Exception` and logged as a
misleading "sync attempt failed" rather than the real cause.

The fix: run_once() guards at the top — if self.writer is None, log an
explicit error ("postgresql enabled but no connection string resolvable")
and return 0 instead of ever reaching the scan/write path.

This also removes the module-level `_get_database_url` (a dead, divergent,
less-safe copy of open_brain.py's resolver — no keychain fallback, no
injection checks) that was never called anywhere in pg_sync.py.

Uses SyncService's injectable config_path / scan_paths constructor seams
and a monkeypatched HOME so the real ~/.claude/logs state file is never
touched. No live DB connection is made anywhere in this test.

Run: python3 -m pytest scripts/tests/test_pg_sync_writer_guard.py -v
"""
import json
import logging
import os
import sys

import pytest

# Add scripts dir to path so we can import pg_sync (the scripts/ copy).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pg_sync  # noqa: E402


def _write_config(path, postgres_enabled=True, connection_string=None):
    pg = {"enabled": postgres_enabled}
    if connection_string:
        pg["connection_string"] = connection_string
    config = {"destinations": {"postgresql": pg}}
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _make_service(tmp_path, monkeypatch, postgres_enabled=True, connection_string=None):
    """Build a SyncService pointed entirely at tmp_path — HOME is redirected
    so state_file (hardcoded to ~/.claude/logs/.pg_sync_state.json) never
    touches the real home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config_path = tmp_path / "auto-logger-config.json"
    _write_config(config_path, postgres_enabled=postgres_enabled, connection_string=connection_string)

    scan_dir = tmp_path / "scan"
    scan_dir.mkdir(exist_ok=True)

    return pg_sync.SyncService(config_path=config_path, scan_paths=[scan_dir])


# ─── writer stays None when no connection string resolves ───────────────────


def test_writer_is_none_when_no_connection_string_resolvable(tmp_path, monkeypatch):
    """Precondition: enabled + no connection_string + no DATABASE_URL -> writer is None."""
    service = _make_service(tmp_path, monkeypatch, postgres_enabled=True, connection_string=None)
    assert service.writer is None


def test_writer_is_set_when_connection_string_resolves(tmp_path, monkeypatch):
    """Sanity check: the guard doesn't fire when a connection string IS resolvable."""
    service = _make_service(
        tmp_path, monkeypatch, postgres_enabled=True,
        connection_string="postgresql://user:pass@host/db",
    )
    assert service.writer is not None


# ─── run_once: None writer -> 0, no crash, no scan ───────────────────────────


def test_run_once_returns_zero_and_does_not_raise_when_writer_is_none(tmp_path, monkeypatch):
    """run_once() must return 0 without raising AttributeError when writer is None."""
    service = _make_service(tmp_path, monkeypatch, postgres_enabled=True, connection_string=None)
    assert service.writer is None  # precondition

    result = service.run_once()

    assert result == 0


def test_run_once_logs_explicit_error_when_writer_is_none(tmp_path, monkeypatch, caplog):
    """The guard must log an explicit, honest error rather than a misleading
    'sync attempt failed' produced by an unguarded AttributeError."""
    service = _make_service(tmp_path, monkeypatch, postgres_enabled=True, connection_string=None)

    with caplog.at_level(logging.ERROR, logger="pg_sync"):
        service.run_once()

    assert any(
        "connection string" in record.message.lower()
        for record in caplog.records
    ), f"Expected an explicit no-connection-string error log; got: {[r.message for r in caplog.records]}"


def test_run_once_does_not_scan_projects_when_writer_is_none(tmp_path, monkeypatch):
    """The None-writer guard must short-circuit BEFORE scanning for projects —
    scanning/reading log files is pointless work if there's nowhere to write."""
    service = _make_service(tmp_path, monkeypatch, postgres_enabled=True, connection_string=None)

    calls = []
    service.scanner.find_projects_with_logs = lambda: calls.append(1) or []

    service.run_once()

    assert not calls, "find_projects_with_logs must not be called when writer is None"


# ─── dead code removal: _get_database_url no longer exists ──────────────────


def test_get_database_url_function_removed():
    """The dead, divergent _get_database_url module-level function must be gone."""
    assert not hasattr(pg_sync, "_get_database_url"), (
        "pg_sync._get_database_url is dead code and should have been removed"
    )
