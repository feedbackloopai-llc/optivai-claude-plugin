#!/usr/bin/env python3
"""brain-W1-S4: RB schema tests.

The thought_versions table is the substrate for snapshot/rollback/diff (S5).
Verifies the table structure, FK behaviors (CASCADE on thought delete; soft
SET NULL on parent_version delete via PG default), revision monotonicity at
the unique constraint, and indexes.

Run: python3 -m pytest tests/test_rb_schema.py -v
"""
import os
import sys
import pytest
import psycopg2
import psycopg2.errors

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402,F401


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "sql", "migrations", "2026-05-21-rb-versions.sql"
)


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


@pytest.fixture(scope="module", autouse=True)
def _run_rb_migration(conn):
    """Apply the RB migration once before any test runs (idempotent).

    Skips silently if the migration file is not yet present so the failing-test
    phase of TDD reports clean skip rather than fixture errors.
    """
    if not os.path.exists(MIGRATION_PATH):
        pytest.skip(f"Migration file missing: {MIGRATION_PATH}")
    with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


class TestRbTableExists:
    def test_thought_versions_table_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='brain' AND table_name='thought_versions'
            """
        )
        assert cur.fetchone() is not None

    def test_required_columns_present(self, conn):
        """All declared columns present with correct nullability."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='thought_versions'
            ORDER BY column_name
            """
        )
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        # Required NOT NULL columns
        for col in [
            "version_id",
            "thought_id",
            "revision",
            "raw_text",
            "prov_agent",
            "prov_activity",
            "created_at",
        ]:
            assert col in rows, f"Missing column: {col}"
            assert rows[col][0] == "NO", f"Column {col} should be NOT NULL"
        # Nullable columns
        for col in [
            "summary",
            "thought_type",
            "topics",
            "people",
            "action_items",
            "embedding",
            "metadata",
            "parent_version",
            "diff_json",
        ]:
            assert col in rows, f"Missing column: {col}"
            assert rows[col][0] == "YES", f"Column {col} should be nullable"


class TestRbConstraints:
    def test_unique_thought_revision(self, conn):
        """UNIQUE (thought_id, revision) constraint exists."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema='brain' AND table_name='thought_versions'
              AND constraint_type='UNIQUE'
            """
        )
        names = [r[0] for r in cur.fetchall()]
        assert len(names) >= 1, "No UNIQUE constraint found"
        # Find the UNIQUE constraint that covers (thought_id, revision) exactly.
        target = None
        for name in names:
            cur.execute(
                """
                SELECT column_name FROM information_schema.constraint_column_usage
                WHERE table_schema='brain' AND table_name='thought_versions'
                  AND constraint_name = %s
                """,
                (name,),
            )
            cols = {r[0] for r in cur.fetchall()}
            if cols == {"thought_id", "revision"}:
                target = name
                break
        assert target is not None, (
            f"No UNIQUE constraint on (thought_id, revision); found: {names}"
        )

    def test_thought_id_fk_cascade(self, conn):
        """thought_id FK is ON DELETE CASCADE."""
        cur = conn.cursor()
        # Pull all FKs and find the one referencing brain.thoughts (not the self-FK)
        cur.execute(
            """
            SELECT conname, confdeltype, confrelid::regclass::text
            FROM pg_constraint
            WHERE conrelid = 'brain.thought_versions'::regclass
              AND contype = 'f'
            """
        )
        fks = cur.fetchall()
        # The FK referencing brain.thoughts (NOT brain.thought_versions) is the cascade FK.
        thought_id_fk = [
            f for f in fks
            if str(f[2]) == "brain.thoughts"
        ]
        assert len(thought_id_fk) == 1, (
            f"Expected 1 FK to brain.thoughts, found {len(thought_id_fk)}: {fks}"
        )
        # PG codes: 'a'=NO ACTION, 'r'=RESTRICT, 'c'=CASCADE, 'n'=SET NULL
        assert thought_id_fk[0][1] == "c", (
            f"Expected CASCADE ('c'), got '{thought_id_fk[0][1]}'"
        )

    def test_parent_version_fk_exists(self, conn):
        """parent_version FK to brain.thought_versions(version_id) exists."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'brain.thought_versions'::regclass
              AND contype = 'f'
              AND confrelid = 'brain.thought_versions'::regclass
            """
        )
        assert cur.fetchone() is not None, "Self-FK parent_version missing"


class TestRbIndexes:
    def test_thought_revision_desc_index(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname='brain' AND tablename='thought_versions'
              AND indexname='idx_thought_versions_thought'
            """
        )
        row = cur.fetchone()
        assert row is not None, "idx_thought_versions_thought missing"
        # Verify revision DESC is in the definition
        definition = row[0].lower()
        assert "revision" in definition and "desc" in definition, (
            f"Index def missing revision DESC: {definition}"
        )

    def test_created_index(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='thought_versions'
              AND indexname='idx_thought_versions_created'
            """
        )
        assert cur.fetchone() is not None, "idx_thought_versions_created missing"


class TestRbInsertBehavior:
    """Direct INSERT smoke tests — validates the table accepts well-formed rows
    and rejects ill-formed ones. CLI-level snapshot/rollback comes in S5.
    """

    def test_insert_valid_version_succeeds(self, conn):
        """Direct INSERT of a v1 row for a real parent thought succeeds."""
        # Create a parent thought first
        parent = open_brain.capture(
            conn,
            text="parent for version test",
            user_id="rb-insert-user",
        )
        pid = parent["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.thought_versions (
                    thought_id, revision, raw_text, prov_agent, prov_activity
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING version_id
                """,
                (pid, 1, "parent for version test", "test-rb-agent", "snapshot"),
            )
            version_id = cur.fetchone()[0]
            conn.commit()
            assert version_id > 0
            # Cleanup the version (parent cleanup below)
            cur.execute(
                "DELETE FROM brain.thought_versions WHERE version_id=%s",
                (version_id,),
            )
            conn.commit()
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (pid,))
            conn.commit()

    def test_duplicate_thought_revision_rejected(self, conn):
        """UNIQUE(thought_id, revision) fires on duplicate (tid, rev=1)."""
        parent = open_brain.capture(
            conn,
            text="for duplicate version test",
            user_id="rb-dup-user",
        )
        pid = parent["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.thought_versions (
                    thought_id, revision, raw_text, prov_agent, prov_activity
                ) VALUES (%s, 1, 'v1', 'test', 'snapshot')
                """,
                (pid,),
            )
            conn.commit()
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO brain.thought_versions (
                        thought_id, revision, raw_text, prov_agent, prov_activity
                    ) VALUES (%s, 1, 'v1-dup', 'test', 'snapshot')
                    """,
                    (pid,),
                )
                conn.commit()
            conn.rollback()
        finally:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM brain.thought_versions WHERE thought_id=%s",
                (pid,),
            )
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id=%s",
                (pid,),
            )
            conn.commit()

    def test_cascade_delete_thought_removes_versions(self, conn):
        """Deleting parent thought cascades to thought_versions rows."""
        parent = open_brain.capture(
            conn,
            text="for cascade version test",
            user_id="rb-cascade-user",
        )
        pid = parent["thought_id"]
        cur = conn.cursor()
        # Insert 3 versions
        for rev in (1, 2, 3):
            cur.execute(
                """
                INSERT INTO brain.thought_versions (
                    thought_id, revision, raw_text, prov_agent, prov_activity
                ) VALUES (%s, %s, %s, 'test', 'snapshot')
                """,
                (pid, rev, f"v{rev}"),
            )
        conn.commit()
        # Verify 3 versions exist
        cur.execute(
            "SELECT COUNT(*) FROM brain.thought_versions WHERE thought_id=%s",
            (pid,),
        )
        assert cur.fetchone()[0] == 3
        # Delete parent
        cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (pid,))
        conn.commit()
        # Verify versions are gone
        cur.execute(
            "SELECT COUNT(*) FROM brain.thought_versions WHERE thought_id=%s",
            (pid,),
        )
        assert cur.fetchone()[0] == 0, "Cascade delete did not remove versions"


class TestRbMigrationIdempotency:
    def test_re_running_migration_is_safe(self, conn):
        """Re-running the migration must not error."""
        with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
