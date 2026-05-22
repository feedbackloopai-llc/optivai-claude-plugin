"""deploy-S3: tests for scripts/migrate-all.sh idempotent migration runner.

Four test classes:
  TestMigrateAllRunner — script exists, executes, announces all 5 migrations
  TestPostMigrateSchema — all 5 brain tables + PROV columns present after run
  TestMigrateAllIdempotency — double-invocation safe
  TestMigrateAllErrorHandling — missing DATABASE_URL fails cleanly
"""
import os
import sys
import subprocess
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


REPO_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATE_SCRIPT = os.path.join(REPO_DIR, "scripts", "migrate-all.sh")


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


def _run_migrate_all() -> subprocess.CompletedProcess:
    """Invoke migrate-all.sh; return CompletedProcess for assertions."""
    return subprocess.run(
        ["bash", MIGRATE_SCRIPT],
        capture_output=True, text=True,
        env={**os.environ, "REPO_DIR": REPO_DIR},
    )


class TestMigrateAllRunner:
    def test_script_exists_and_is_executable(self):
        assert os.path.exists(MIGRATE_SCRIPT), "migrate-all.sh missing"
        assert os.access(MIGRATE_SCRIPT, os.X_OK), "migrate-all.sh not executable"

    def test_runs_to_completion(self):
        result = _run_migrate_all()
        assert result.returncode == 0, (
            f"migrate-all.sh exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_announces_all_five_migrations(self):
        result = _run_migrate_all()
        for marker in (
            "prov-dm.sql",
            "rb-versions.sql",
            "vf-audit.sql",
            "hebbian-promotions.sql",
            "replay-log.sql",
        ):
            assert marker in result.stdout, f"Migration {marker} not announced"

    def test_dependency_order_in_script_source(self):
        """The script lists migrations in Lin/Li/Chen §12.1 dependency order:
        PV → RB → VF_ε → Hebbian → Replay. Asserting on source ensures the
        ordering is not silently re-shuffled by a future edit.
        """
        with open(MIGRATE_SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        ordered = [
            "prov-dm.sql",
            "rb-versions.sql",
            "vf-audit.sql",
            "hebbian-promotions.sql",
            "replay-log.sql",
        ]
        positions = [source.find(m) for m in ordered]
        assert all(p > 0 for p in positions), (
            f"Not all migration markers found in source: "
            f"{dict(zip(ordered, positions))}"
        )
        assert positions == sorted(positions), (
            f"Migrations out of dependency order in source: "
            f"{dict(zip(ordered, positions))}"
        )


class TestPostMigrateSchema:
    """After migrate-all.sh runs, all 5 expected tables exist."""

    def test_five_brain_tables_present(self, conn):
        _run_migrate_all()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='brain'
              AND table_name IN ('thoughts', 'thought_versions',
                                  'forget_audit', 'promotions', 'replay_log')
        """)
        present = {r[0] for r in cur.fetchall()}
        expected = {"thoughts", "thought_versions", "forget_audit", "promotions", "replay_log"}
        assert present == expected, f"Missing: {expected - present}"

    def test_prov_dm_columns_present_after_migrate(self, conn):
        _run_migrate_all()
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='thoughts'
              AND column_name IN ('prov_agent', 'prov_activity', 'was_generated_by',
                                  'was_derived_from', 'source_uri')
        """)
        present = {r[0] for r in cur.fetchall()}
        assert len(present) == 5, f"Missing PROV columns: {present}"


class TestMigrateAllIdempotency:
    """Re-running migrate-all.sh produces no errors and no changes."""

    def test_double_invocation_safe(self):
        r1 = _run_migrate_all()
        r2 = _run_migrate_all()
        assert r1.returncode == 0 and r2.returncode == 0, (
            f"First run: {r1.returncode}; second run: {r2.returncode}\n"
            f"stderr2: {r2.stderr}"
        )


class TestMigrateAllErrorHandling:
    def test_missing_database_url_fails_cleanly(self):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        result = subprocess.run(
            ["bash", MIGRATE_SCRIPT],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0
        assert "DATABASE_URL" in result.stderr or "DATABASE_URL" in result.stdout
