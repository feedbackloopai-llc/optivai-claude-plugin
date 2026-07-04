#!/usr/bin/env python3
"""Finding 7 (review fix) — log_writer session dedup + bare except regression tests.

_write_session_metadata() checks sessions.jsonl for an existing row matching
self.session_id before appending a new one. Two bugs existed here:

  1. Both the existence-check read and _log_error() used a bare `except:`,
     which also catches KeyboardInterrupt/SystemExit — not just Exception.
  2. If the existence-check read failed for any reason (permissions,
     corrupt file, etc.), the bare `except: pass` silently swallowed the
     error and fell through to the append below, writing a DUPLICATE
     session row even though the session might already be logged.

The fix: `except Exception:` (not bare `except:`) in both spots, and on a
read failure in _write_session_metadata, route through self._log_error(...)
and return WITHOUT appending — never blindly re-append on a failed check.

We build a minimal stand-in for AgentActivityLogger carrying only the
attributes _write_session_metadata touches, calling the method unbound
(mirroring the pattern in test_log_writer_email.py), to avoid the heavy
__init__ (directory creation, real session id generation, etc).

Run: python3 -m pytest scripts/tests/test_log_writer_session_dedup.py -v
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

# Add scripts dir to path so we can import log_writer (the scripts/ copy).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import log_writer  # noqa: E402


def _make_logger_stub(tmp_path, session_id, session_tracking=True):
    """Minimal stand-in carrying only the state _write_session_metadata touches."""
    stub = SimpleNamespace()
    stub.config = {"session_tracking": session_tracking}
    stub.log_dir = tmp_path
    stub.session_id = session_id
    stub.user = "testuser"
    stub.project_dir = tmp_path
    stub.project_name = "testproject"
    stub.provider_env = {"type": "teams", "model": "test-model", "organization": "TestOrg"}
    stub._log_error = lambda msg: None
    return stub


# ─── dedup: existing session must not be re-appended ─────────────────────────


def test_existing_session_not_duplicated(tmp_path):
    """Writing metadata for a session id already present in sessions.jsonl must NOT create a second row."""
    sessions_file = tmp_path / "sessions.jsonl"
    existing_line = json.dumps({"session_id": "session-existing-abc123", "user": "x"})
    sessions_file.write_text(existing_line + "\n", encoding="utf-8")

    stub = _make_logger_stub(tmp_path, "session-existing-abc123")

    log_writer.AgentActivityLogger._write_session_metadata(stub)

    lines = sessions_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, (
        f"Expected exactly 1 line (no duplicate appended), got {len(lines)}: {lines}"
    )


def test_new_session_is_appended(tmp_path):
    """Sanity check: a genuinely new session id IS appended (dedup guard isn't over-broad)."""
    sessions_file = tmp_path / "sessions.jsonl"
    existing_line = json.dumps({"session_id": "session-other-111"})
    sessions_file.write_text(existing_line + "\n", encoding="utf-8")

    stub = _make_logger_stub(tmp_path, "session-new-222")

    log_writer.AgentActivityLogger._write_session_metadata(stub)

    lines = sessions_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, f"Expected new session appended alongside existing, got: {lines}"
    assert json.loads(lines[1])["session_id"] == "session-new-222"


# ─── read failure must not fall through to a duplicate append ───────────────


def test_read_failure_does_not_append_duplicate(tmp_path, monkeypatch):
    """A failed existence-check read must route through _log_error and return,
    NOT silently fall through and append a (possibly duplicate) row."""
    sessions_file = tmp_path / "sessions.jsonl"
    sessions_file.write_text('{"session_id": "session-existing-abc123"}\n', encoding="utf-8")

    errors = []
    stub = _make_logger_stub(tmp_path, "session-existing-abc123")
    stub._log_error = lambda msg: errors.append(msg)

    original_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if os.fspath(path) == str(sessions_file) and "r" in mode:
            raise OSError("simulated read failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    log_writer.AgentActivityLogger._write_session_metadata(stub)

    lines = sessions_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, (
        f"Read failure must NOT result in an appended (duplicate) row, got: {lines}"
    )
    assert errors, "Read failure must be routed through _log_error, not silently swallowed"
    assert "sessions.jsonl" in errors[0]


# ─── bare except: regression — must not swallow KeyboardInterrupt/SystemExit ─


def test_keyboard_interrupt_during_existence_check_is_not_swallowed(tmp_path, monkeypatch):
    """A bare `except:` would swallow KeyboardInterrupt; `except Exception:` must not."""
    sessions_file = tmp_path / "sessions.jsonl"
    sessions_file.write_text('{"session_id": "session-other"}\n', encoding="utf-8")

    stub = _make_logger_stub(tmp_path, "session-new-xyz")

    original_open = open

    def interrupting_open(path, mode="r", *args, **kwargs):
        if os.fspath(path) == str(sessions_file) and "r" in mode:
            raise KeyboardInterrupt()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", interrupting_open)

    with pytest.raises(KeyboardInterrupt):
        log_writer.AgentActivityLogger._write_session_metadata(stub)


def test_log_error_bare_except_does_not_swallow_keyboard_interrupt(tmp_path, monkeypatch):
    """_log_error's own file write must use `except Exception:`, not bare `except:`."""
    stub = SimpleNamespace(log_dir=tmp_path)

    original_open = open

    def interrupting_open(path, mode="r", *args, **kwargs):
        if "a" in mode:
            raise KeyboardInterrupt()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", interrupting_open)

    with pytest.raises(KeyboardInterrupt):
        log_writer.AgentActivityLogger._log_error(stub, "some error")
