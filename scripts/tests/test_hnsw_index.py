#!/usr/bin/env python3
"""fblai-3yd1j — HNSW vector index presence tests.

Verifies:
  (a) BRAIN_SCHEMA_PG.sql contains the active HNSW index DDL (not commented out).
  (b) The migration file 2026-06-11-hnsw-index.sql exists.
  (c) The migration SQL is idempotent (contains CREATE INDEX IF NOT EXISTS).
  (d) The live DB (if accessible via DATABASE_URL) has the index in pg_indexes.

Tests (a)-(c) are offline/static and never require DATABASE_URL.
Test (d) is skipped when DATABASE_URL is absent.

Run: python3 -m pytest scripts/tests/test_hnsw_index.py -v
"""
import os
import sys
from pathlib import Path

import pytest

# Locate repo root relative to this test file
_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_SQL_DIR = _REPO_ROOT / "sql"
_MIGRATIONS_DIR = _SQL_DIR / "migrations"
_SCHEMA_FILE = _SQL_DIR / "BRAIN_SCHEMA_PG.sql"
_MIGRATION_FILE = _MIGRATIONS_DIR / "2026-06-11-hnsw-index.sql"

sys.path.insert(0, str(_SCRIPTS_DIR))


# ─── (a) Schema file contains the active HNSW index ─────────────────────────

def test_schema_contains_hnsw_index():
    """BRAIN_SCHEMA_PG.sql must contain an active (non-commented) HNSW index DDL."""
    assert _SCHEMA_FILE.exists(), f"Schema file not found: {_SCHEMA_FILE}"
    schema = _SCHEMA_FILE.read_text(encoding="utf-8")

    # Check for HNSW keyword on a non-commented line
    hnsw_active_lines = [
        line for line in schema.splitlines()
        if "hnsw" in line.lower() and not line.strip().startswith("--")
    ]
    assert len(hnsw_active_lines) > 0, (
        "Expected at least one active (non-commented) HNSW index line in "
        "BRAIN_SCHEMA_PG.sql; found none.  The IVFFlat stub must have been "
        "replaced with an HNSW CREATE INDEX IF NOT EXISTS statement."
    )

    # Check specifically for idx_thoughts_embedding_hnsw
    assert any("idx_thoughts_embedding_hnsw" in line for line in hnsw_active_lines), (
        "Expected idx_thoughts_embedding_hnsw in the active HNSW index DDL; "
        f"active HNSW lines found: {hnsw_active_lines!r}"
    )


def test_schema_does_not_have_only_commented_ivfflat():
    """The old IVFFlat stub (commented-out) should no longer be the only vector index."""
    assert _SCHEMA_FILE.exists(), f"Schema file not found: {_SCHEMA_FILE}"
    schema = _SCHEMA_FILE.read_text(encoding="utf-8")

    # Count active vector index lines (non-commented lines referencing ivfflat or hnsw)
    active_vector_index_lines = [
        line for line in schema.splitlines()
        if not line.strip().startswith("--")
        and ("ivfflat" in line.lower() or "hnsw" in line.lower())
        and "CREATE INDEX" in line.upper()
    ]
    assert len(active_vector_index_lines) >= 1, (
        "Expected at least one active vector index (IVFFlat or HNSW) in schema; "
        "found only commented-out stubs."
    )


# ─── (b) Migration file exists ───────────────────────────────────────────────

def test_hnsw_migration_file_exists():
    """The migration file 2026-06-11-hnsw-index.sql must exist."""
    assert _MIGRATION_FILE.exists(), (
        f"Migration file not found: {_MIGRATION_FILE}\n"
        "Expected at sql/migrations/2026-06-11-hnsw-index.sql"
    )


# ─── (c) Migration is idempotent ─────────────────────────────────────────────

def test_hnsw_migration_is_idempotent():
    """The migration SQL must use CREATE INDEX IF NOT EXISTS (idempotent)."""
    assert _MIGRATION_FILE.exists(), f"Migration file not found: {_MIGRATION_FILE}"
    migration_sql = _MIGRATION_FILE.read_text(encoding="utf-8").upper()

    assert "CREATE INDEX IF NOT EXISTS" in migration_sql, (
        "Migration must use CREATE INDEX IF NOT EXISTS for idempotency; "
        f"got migration content (uppercased):\n{migration_sql[:500]}"
    )

    assert "HNSW" in migration_sql, (
        "Migration must reference HNSW index type; "
        f"got:\n{migration_sql[:500]}"
    )

    assert "IDX_THOUGHTS_EMBEDDING_HNSW" in migration_sql, (
        "Migration must name the index idx_thoughts_embedding_hnsw; "
        f"got:\n{migration_sql[:500]}"
    )


# ─── (d) Live DB has the index (skipped when no DATABASE_URL) ────────────────

def test_hnsw_index_in_live_db():
    """Query pg_indexes to confirm idx_thoughts_embedding_hnsw exists after migration.

    Skipped when DATABASE_URL is absent.
    """
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skipping live DB check")

    import open_brain  # noqa: E402

    try:
        conn = open_brain._connect()
    except Exception as exc:
        pytest.skip(f"Could not connect to DB: {exc}")

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'brain'
              AND tablename = 'thoughts'
              AND indexname = 'idx_thoughts_embedding_hnsw'
            """
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    assert len(rows) == 1, (
        "Expected idx_thoughts_embedding_hnsw in pg_indexes for brain.thoughts; "
        "the HNSW migration may not have been applied.  "
        "Run: python3 scripts/open_brain.py --migrate sql/migrations/2026-06-11-hnsw-index.sql"
    )
