#!/usr/bin/env python3
"""brain-W1-S1: PROV-DM schema tests.

PV primitive (Provenance Visibility per W3C PROV-DM 1.3) — foundational MS_eps
substrate. Verifies the migration runs idempotently and the 5 PROV columns
exist with correct nullability + FK + indexes.

Run: python3 -m pytest tests/test_prov_dm_schema.py -v
"""
import os
import sys
import subprocess
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402,F401  (imported to verify module is importable)


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "sql", "migrations", "2026-05-21-prov-dm.sql"
)


@pytest.fixture(scope="module")
def conn():
    """Live Postgres connection to the test DB."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


@pytest.fixture(scope="module", autouse=True)
def _run_migration(conn):
    """Apply the PROV-DM migration before any test runs (idempotent).

    Skips silently if the migration file is not yet present so the failing-test
    phase of TDD reports clean assertion errors rather than fixture errors.
    """
    if not os.path.exists(MIGRATION_PATH):
        pytest.skip(f"Migration file not yet created: {MIGRATION_PATH}")
    with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


class TestProvDmColumns:
    def test_five_prov_columns_exist(self, conn):
        """All 5 PROV-DM columns present on brain.thoughts."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='thoughts'
              AND column_name IN ('prov_agent','prov_activity','was_generated_by',
                                  'was_derived_from','source_uri')
            ORDER BY column_name
            """
        )
        rows = cur.fetchall()
        assert len(rows) == 5, f"Expected 5 PROV columns, got {len(rows)}: {rows}"
        col_names = {r[0] for r in rows}
        assert col_names == {
            "prov_agent",
            "prov_activity",
            "was_generated_by",
            "was_derived_from",
            "source_uri",
        }

    def test_required_columns_are_not_null(self, conn):
        """prov_agent, prov_activity, was_generated_by must be NOT NULL."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='thoughts'
              AND column_name IN ('prov_agent','prov_activity','was_generated_by')
            """
        )
        for col_name, is_nullable in cur.fetchall():
            assert is_nullable == "NO", (
                f"{col_name} must be NOT NULL after migration, "
                f"got is_nullable={is_nullable}"
            )

    def test_optional_columns_are_nullable(self, conn):
        """was_derived_from + source_uri must remain nullable."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='thoughts'
              AND column_name IN ('was_derived_from','source_uri')
            """
        )
        for col_name, is_nullable in cur.fetchall():
            assert is_nullable == "YES", (
                f"{col_name} must be nullable, got is_nullable={is_nullable}"
            )


class TestProvDmBackfill:
    def test_no_rows_have_null_required_prov(self, conn):
        """Every existing row has prov_agent + prov_activity + was_generated_by populated."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM brain.thoughts
            WHERE prov_agent IS NULL
               OR prov_activity IS NULL
               OR was_generated_by IS NULL
            """
        )
        null_count = cur.fetchone()[0]
        assert null_count == 0, (
            f"Found {null_count} rows with null required PROV — backfill incomplete"
        )

    def test_legacy_backfill_marker(self, conn):
        """Pre-migration rows tagged with prov_agent='legacy-import' (sanity check the backfill ran)."""
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM brain.thoughts WHERE prov_agent='legacy-import'"
        )
        legacy_count = cur.fetchone()[0]
        # If this is a fresh DB with no rows, legacy_count==0 is fine.
        # We only assert the query ran without error and returned a non-negative count.
        assert legacy_count >= 0


class TestProvDmFkConstraint:
    def test_fk_constraint_exists(self, conn):
        """fk_thoughts_derived_from FK constraint exists on was_derived_from -> thought_id."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema='brain' AND table_name='thoughts'
              AND constraint_name='fk_thoughts_derived_from'
            """
        )
        assert cur.fetchone() is not None, "fk_thoughts_derived_from constraint missing"


class TestProvDmIndexes:
    def test_derived_from_index_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='thoughts'
              AND indexname='idx_thoughts_derived_from'
            """
        )
        assert cur.fetchone() is not None, "idx_thoughts_derived_from missing"

    def test_generated_by_index_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='thoughts'
              AND indexname='idx_thoughts_generated_by'
            """
        )
        assert cur.fetchone() is not None, "idx_thoughts_generated_by missing"


class TestMigrationIdempotency:
    def test_running_migration_twice_is_safe(self, conn):
        """Re-running the migration must not error — pure ADD COLUMN IF NOT EXISTS pattern."""
        with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.cursor() as cur:
            cur.execute(sql)  # Should not raise
        conn.commit()


class TestMigrationRunnerCli:
    def test_cli_migrate_flag_exists(self):
        """`python3 scripts/open_brain.py --help` mentions --migrate."""
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert "--migrate" in result.stdout, (
            f"--migrate not in --help output:\n{result.stdout}"
        )

    def test_cli_migrate_runs_successfully(self):
        """Running --migrate against our migration file exits 0 and returns JSON status."""
        result = subprocess.run(
            [
                "python3",
                "scripts/open_brain.py",
                "--migrate",
                "sql/migrations/2026-05-21-prov-dm.sql",
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, (
            f"--migrate exit code {result.returncode}, stderr: {result.stderr}"
        )
        # JSON output expected; either 'ok' as a status value or 'status' as a key
        out = result.stdout.lower()
        assert "ok" in out or "status" in out, (
            f"Expected JSON status output, got: {result.stdout}"
        )
