#!/usr/bin/env python3
"""fblai-4ml0h / fblai-717kv / fblai-c7hec — Migration confinement + hardlink/size hardening tests.

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


# ─── Test (e): hardlinked file inside sql/ is rejected (fblai-717kv) ─────────


def test_run_migration_rejects_hardlinked_file(tmp_path):
    """A hardlinked file inside sql/ must be rejected (st_nlink > 1 guard).

    Path.resolve() follows symlinks but NOT hardlinks — a hardlink inside sql/
    to an external inode passes the containment check without the st_nlink fix.
    """
    sql_dir = _sql_dir()
    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}")

    # Create a legitimate-looking SQL file inside sql/
    inner_file = sql_dir / "_hardlink_test_target.sql"
    inner_file.write_text("-- hardlink test target\nSELECT 1;\n")

    # Create a hardlink to it (also inside sql/ — the inode now has nlink=2)
    hardlink_path = sql_dir / "_hardlink_test_link.sql"
    try:
        os.link(inner_file, hardlink_path)
    except OSError:
        try:
            inner_file.unlink()
        except OSError:
            pass
        pytest.skip("Cannot create hardlinks on this filesystem")

    fake_cur = mock.MagicMock()
    fake_conn = mock.MagicMock()
    fake_conn.cursor.return_value.__enter__ = mock.MagicMock(return_value=fake_cur)
    fake_conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn):
            with pytest.raises(RuntimeError) as exc_info:
                open_brain.run_migration(str(hardlink_path))
    finally:
        for p in (hardlink_path, inner_file):
            try:
                p.unlink()
            except OSError:
                pass

    # DB must NOT have been touched
    fake_cur.execute.assert_not_called()

    error_msg = str(exc_info.value).lower()
    assert any(word in error_msg for word in ("hardlink", "hard link", "nlink", "refusing")), (
        f"Error should describe the hardlink rejection, got: {exc_info.value!r}"
    )


def test_run_migration_rejects_hardlinked_file_via_stat_mock():
    """Unit test: mock os.stat to return st_nlink=2 → rejection, no DB execute.

    Creates a real small file inside sql/ so the containment check passes, then
    patches os.stat to simulate st_nlink=2 (hardlinked inode).  The file is
    cleaned up regardless of outcome.
    """
    sql_dir = _sql_dir()
    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}")

    # Use a real file inside sql/ so containment check passes without mocking Path.
    candidate = sql_dir / "_hardlink_stat_mock_test.sql"
    candidate.write_text("SELECT 1;")

    fake_conn = mock.MagicMock()

    fake_stat = mock.MagicMock()
    fake_stat.st_nlink = 2
    fake_stat.st_size = 100

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn), \
             mock.patch("os.stat", return_value=fake_stat):
            with pytest.raises(RuntimeError) as exc_info:
                open_brain.run_migration(str(candidate))
    finally:
        try:
            candidate.unlink()
        except OSError:
            pass

    error_msg = str(exc_info.value).lower()
    assert any(word in error_msg for word in ("hardlink", "hard link", "nlink", "refusing")), (
        f"Error should describe the hardlink rejection, got: {exc_info.value!r}"
    )
    # DB must NOT have been touched
    fake_conn.cursor.return_value.execute.assert_not_called()


def test_run_migration_rejects_oversized_file(tmp_path):
    """A file reported as > MAX_MIGRATION_BYTES must be rejected before read (fblai-c7hec).

    We mock os.stat so the file appears oversized without creating a 10MB file.
    A real small file inside sql/ is used so the containment check passes.
    """
    sql_dir = _sql_dir()
    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}")

    # Create a small (real) file inside sql/ so containment check passes.
    oversized_candidate = sql_dir / "_oversized_test.sql"
    oversized_candidate.write_text("SELECT 1;")

    fake_conn = mock.MagicMock()

    # Patch os.stat to return st_size > MAX_MIGRATION_BYTES and st_nlink=1
    fake_stat = mock.MagicMock()
    fake_stat.st_nlink = 1
    fake_stat.st_size = open_brain.MAX_MIGRATION_BYTES + 1

    try:
        with mock.patch("open_brain._connect", return_value=fake_conn), \
             mock.patch("os.stat", return_value=fake_stat):
            with pytest.raises(RuntimeError) as exc_info:
                open_brain.run_migration(str(oversized_candidate))
    finally:
        try:
            oversized_candidate.unlink()
        except OSError:
            pass

    error_msg = str(exc_info.value).lower()
    assert any(word in error_msg for word in ("too large", "oversized", "limit", "bytes", "mib")), (
        f"Error should describe the size rejection, got: {exc_info.value!r}"
    )

    # Verify the file was NOT opened / read (no DB execute either)
    fake_conn.cursor.return_value.execute.assert_not_called()


def test_run_migration_normal_small_file_proceeds(tmp_path):
    """A normal small file (st_nlink=1, size<limit) inside sql/ proceeds normally (regression)."""
    sql_dir = _sql_dir()
    if not sql_dir.exists():
        pytest.skip(f"sql/ dir not found at {sql_dir}")

    normal_file = sql_dir / "_normal_test_proceed.sql"
    normal_file.write_text("-- normal test\nSELECT 1;\n")

    # run_migration uses `with conn.cursor() as cur:` — set up context manager.
    fake_cur = mock.MagicMock()
    fake_cur_ctx = mock.MagicMock()
    fake_cur_ctx.__enter__ = mock.MagicMock(return_value=fake_cur)
    fake_cur_ctx.__exit__ = mock.MagicMock(return_value=False)
    fake_conn = mock.MagicMock()
    fake_conn.cursor.return_value = fake_cur_ctx

    # Real stat: st_nlink=1, small size (let os.stat run on the real file)
    try:
        with mock.patch("open_brain._connect", return_value=fake_conn):
            result = open_brain.run_migration(str(normal_file))
    finally:
        try:
            normal_file.unlink()
        except OSError:
            pass

    assert result.get("status") == "ok", f"Expected status=ok, got: {result!r}"
    fake_cur.execute.assert_called_once()


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
