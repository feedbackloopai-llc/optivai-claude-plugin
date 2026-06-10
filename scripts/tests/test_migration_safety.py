#!/usr/bin/env python3
"""fblai-4ml0h — Migration confinement + curl|sh removal tests.

Verifies:
  (a) run_migration("/tmp/evil.sql") (path outside sql/) → rejected with
      RuntimeError/error, no DB execute (mocked cursor).
  (b) run_migration with a symlink in sql/ pointing outside → rejected.
  (c) a legit path inside sql/ → proceeds (mock conn/execute, assert called).
  (d) _ensure_ollama_ready source contains no "curl" and no "install.sh".

Tests do NOT require DATABASE_URL — the connection is mocked for (a)-(c).

Run: python3 -m pytest scripts/tests/test_migration_safety.py -v
"""
import inspect
import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Helper: locate the canonical sql/ dir ───────────────────────────────────

def _sql_dir() -> Path:
    """Return the expected sql/ directory (sibling of scripts/)."""
    # open_brain.py lives at <repo>/scripts/open_brain.py
    # sql/ is at <repo>/sql/
    script_path = Path(open_brain.__file__).resolve()
    return (script_path.parent.parent / "sql").resolve()


# ─── Test (a): path outside sql/ is rejected ─────────────────────────────────


def test_run_migration_rejects_arbitrary_path():
    """run_migration('/tmp/evil.sql') must be rejected before any DB execute."""
    fake_cur = mock.MagicMock()
    fake_conn = mock.MagicMock()
    fake_conn.cursor.return_value.__enter__ = mock.MagicMock(return_value=fake_cur)
    fake_conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)

    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as f:
        f.write(b"DROP TABLE IF EXISTS evil;")
        evil_path = f.name

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn):
            with pytest.raises((RuntimeError, ValueError, PermissionError)) as exc_info:
                open_brain.run_migration(evil_path)
    finally:
        os.unlink(evil_path)

    # DB must NOT have been touched
    fake_cur.execute.assert_not_called()

    error_msg = str(exc_info.value).lower()
    assert any(word in error_msg for word in ("not allowed", "outside", "confined", "invalid", "permission", "forbidden", "sql")), (
        f"Error message should describe the confinement violation, got: {exc_info.value!r}"
    )


# ─── Test (b): symlink inside sql/ pointing outside is rejected ───────────────


def test_run_migration_rejects_symlink_outside_sql(tmp_path):
    """A symlink inside sql/ that points outside the sql/ dir must be rejected.

    resolve() follows the symlink before the is_relative_to check, so the
    real path is outside sql/ and must be rejected.
    """
    sql_dir = _sql_dir()

    # Create an evil file outside sql/
    evil_target = tmp_path / "evil_target.sql"
    evil_target.write_text("DROP TABLE IF EXISTS evil;")

    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}; skipping symlink test")

    # Create the symlink inside sql/ pointing to the evil target
    symlink_path = sql_dir / "evil_symlink_test.sql"
    try:
        symlink_path.symlink_to(evil_target)
    except (OSError, NotImplementedError):
        pytest.skip("Cannot create symlinks on this platform")

    fake_cur = mock.MagicMock()
    fake_conn = mock.MagicMock()
    fake_conn.cursor.return_value.__enter__ = mock.MagicMock(return_value=fake_cur)
    fake_conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn):
            with pytest.raises((RuntimeError, ValueError, PermissionError)):
                open_brain.run_migration(str(symlink_path))
    finally:
        try:
            symlink_path.unlink()
        except OSError:
            pass

    # DB must NOT have been touched
    fake_cur.execute.assert_not_called()


# ─── Test (c): legitimate path inside sql/ proceeds ──────────────────────────


def test_run_migration_accepts_legitimate_sql_path():
    """A real file inside sql/ must be accepted and DB execute must be called."""
    sql_dir = _sql_dir()

    # Use the canonical schema file if it exists; otherwise create a temp one
    # inside sql/ for the duration of the test.
    real_sql_file = sql_dir / "BRAIN_SCHEMA_PG.sql"
    created_temp = False

    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}")

    if not real_sql_file.exists():
        # Create a minimal SQL file inside sql/ just for this test
        real_sql_file = sql_dir / "_test_migration_temp.sql"
        real_sql_file.write_text("-- test migration\nSELECT 1;\n")
        created_temp = True

    fake_cur = mock.MagicMock()
    fake_conn = mock.MagicMock()
    fake_conn.cursor.return_value.__enter__ = mock.MagicMock(return_value=fake_cur)
    fake_conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn):
            result = open_brain.run_migration(str(real_sql_file))
    finally:
        if created_temp:
            try:
                real_sql_file.unlink()
            except OSError:
                pass

    # DB execute must have been called
    fake_cur.execute.assert_called_once()

    assert result.get("status") == "ok", (
        f"Expected status='ok' for a legitimate migration, got: {result!r}"
    )


# ─── Test (d): _ensure_ollama_ready has no curl|sh auto-install ──────────────


def test_ensure_ollama_ready_has_no_curl_install():
    """_ensure_ollama_ready must contain no 'curl' and no 'install.sh' references.

    This is a source-level assertion that the auto-install block was removed.
    """
    source = inspect.getsource(open_brain._ensure_ollama_ready)

    assert "curl" not in source, (
        "Found 'curl' in _ensure_ollama_ready source — the curl|sh auto-install "
        "must be removed entirely.\n"
        f"Source:\n{source}"
    )
    assert "install.sh" not in source, (
        "Found 'install.sh' in _ensure_ollama_ready source — the curl|sh "
        "auto-install must be removed entirely.\n"
        f"Source:\n{source}"
    )
