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
import psycopg2.errors  # for ForeignKeyViolation (F4 behavioral tests)

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
        """Backfill discipline: pre-migration rows tagged with prov_agent='legacy-import'
        AND prov_activity='unknown' AND was_generated_by='activity-legacy-{thought_id}'.

        If table is empty (fresh DB), the test skips. If rows exist, the backfill must
        have populated all three required fields with the legacy markers.
        """
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM brain.thoughts")
        total = cur.fetchone()[0]
        if total == 0:
            pytest.skip("Empty thoughts table — backfill marker test not applicable")

        # If any pre-S2 row exists (i.e., captured before --capture knew about PROV),
        # it must carry the legacy markers. Post-S2 rows have proper PROV from capture flow.
        # We detect pre-S2 rows by the legacy marker itself.
        cur.execute(
            """
            SELECT COUNT(*) FROM brain.thoughts
            WHERE prov_agent = 'legacy-import'
            """
        )
        legacy_count = cur.fetchone()[0]

        if legacy_count == 0:
            # All rows are post-S2 captures — that's fine.
            # Just assert that POST-S2 rows do NOT carry the legacy marker on prov_activity.
            cur.execute(
                """
                SELECT COUNT(*) FROM brain.thoughts
                WHERE prov_activity = 'unknown'
                """
            )
            unknown_count = cur.fetchone()[0]
            assert unknown_count == 0, (
                f"Post-S2 rows should have specific prov_activity (capture / auto-capture-* / etc), "
                f"not 'unknown'. Found {unknown_count} rows with prov_activity='unknown'."
            )
            return

        # Legacy rows present — verify they carry the matching trio of legacy markers
        cur.execute(
            """
            SELECT thought_id, prov_agent, prov_activity, was_generated_by
            FROM brain.thoughts
            WHERE prov_agent = 'legacy-import'
            LIMIT 5
            """
        )
        sample = cur.fetchall()
        for tid, p_agent, p_activity, w_gen in sample:
            assert p_agent == 'legacy-import', f"Row {tid} has prov_agent={p_agent}"
            assert p_activity == 'unknown', (
                f"Row {tid} has prov_activity={p_activity}, expected 'unknown'"
            )
            assert w_gen == f"activity-legacy-{tid}", (
                f"Row {tid} has was_generated_by={w_gen}, expected 'activity-legacy-{tid}'"
            )


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


class TestFkAndIndexBehavior:
    """F4 closure: verify the FK constraint actually FIRES and ON DELETE SET NULL
    behaves correctly. Verify the partial index excludes NULL rows.
    """

    def test_fk_rejects_nonexistent_derived_from_at_insert(self, conn):
        """Direct SQL INSERT with bogus was_derived_from must fail FK constraint.
        capture() validates BEFORE insert; this tests the DB-level enforcement.
        """
        # Use a unique tid so we don't pollute
        cur = conn.cursor()
        try:
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO brain.thoughts (
                        thought_id, user_id, raw_text, summary, thought_type,
                        topics, people, action_items, source,
                        prov_agent, prov_activity, was_generated_by, was_derived_from
                    ) VALUES (
                        %s, %s, 'fk-test', 'fk-test', 'insight',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'manual',
                        'cli-user-fktest', 'capture', %s,
                        %s
                    )
                    """,
                    (
                        "brain-fk-test-001",
                        "fktest-user",
                        "activity-brain-fk-test-001",
                        "nonexistent-parent-id-xyz",
                    ),
                )
                conn.commit()
        finally:
            conn.rollback()  # Always rollback whether the INSERT succeeded or raised

    def test_on_delete_set_null_sets_child_to_null(self, conn):
        """When a parent thought is deleted, child's was_derived_from is set to NULL,
        not cascade-deleted (preserves citation chain history).
        """
        # Create parent
        parent = open_brain.capture(
            conn, text="parent for cascade test", user_id="ondelete-test"
        )
        pid = parent["thought_id"]
        # Create child derived from parent
        child = open_brain.capture(
            conn,
            text="child for cascade test",
            user_id="ondelete-test",
            was_derived_from=pid,
        )
        cid = child["thought_id"]
        try:
            # Delete the parent
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id = %s", (pid,))
            conn.commit()
            # Verify child still exists AND its was_derived_from is NULL
            cur.execute(
                "SELECT was_derived_from FROM brain.thoughts WHERE thought_id = %s",
                (cid,),
            )
            row = cur.fetchone()
            assert row is not None, "Child should NOT be cascade-deleted"
            assert row[0] is None, (
                f"Child's was_derived_from should be NULL after parent delete, got {row[0]}"
            )
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id = %s", (cid,))
            conn.commit()

    def test_partial_index_excludes_null_rows(self, conn):
        """idx_thoughts_derived_from is a partial index WHERE was_derived_from IS NOT NULL.
        Verify via pg_indexes that the WHERE clause is present.
        """
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname='brain' AND tablename='thoughts'
              AND indexname='idx_thoughts_derived_from'
            """
        )
        row = cur.fetchone()
        assert row is not None, "Partial index missing"
        indexdef = row[0].lower()
        # The partial WHERE clause must be in the index definition
        assert "where" in indexdef and "is not null" in indexdef, (
            f"Index is not partial — full definition: {indexdef}"
        )

    def test_fk_constraint_action_is_set_null(self, conn):
        """Verify the FK action is ON DELETE SET NULL (not CASCADE, not RESTRICT)."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT confdeltype
            FROM pg_constraint
            WHERE conname = 'fk_thoughts_derived_from'
            """
        )
        row = cur.fetchone()
        assert row is not None, "FK constraint not found"
        # PG codes: 'a'=NO ACTION, 'r'=RESTRICT, 'c'=CASCADE, 'n'=SET NULL, 'd'=SET DEFAULT
        assert row[0] == 'n', f"FK action expected SET NULL ('n'), got '{row[0]}'"
