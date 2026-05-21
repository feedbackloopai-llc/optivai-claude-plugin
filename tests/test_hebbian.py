#!/usr/bin/env python3
"""brain-W1-S12: Hebbian promotion tests.

Verifies S10 (brain.promotions schema) + S11 (--promote / --demote CLI +
compute_effective_weight helper) + the time-decay math + the within-kind
cross-user isolation defense.

Hebbian formula:
    effective_weight = sum( weight * (1 + days_since_promoted)^(-0.7) )

Reference: Lin/Li/Chen 2026 §12.1 ("Hebbian agent-controlled metacognition");
OptivAI builder neurosymbolic harness (memory_promotions table, time-decay
exponent -0.7).

Run: DATABASE_URL=postgres://... python3 -m pytest tests/test_hebbian.py -v
"""
import os
import sys
import subprocess

import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "sql", "migrations", "2026-05-21-hebbian-promotions.sql"
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
def _run_hebbian_migration(conn):
    """Apply the Hebbian migration once before any test runs (idempotent).

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


def _cleanup_thought(conn, thought_id):
    """Best-effort row teardown. CASCADE removes related promotions."""
    cur = conn.cursor()
    cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (thought_id,))
    conn.commit()


class TestHebbianSchema:
    def test_promotions_table_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='brain' AND table_name='promotions'
            """
        )
        assert cur.fetchone() is not None

    def test_required_columns_present(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='promotions'
            ORDER BY column_name
            """
        )
        rows = {r[0]: r[1] for r in cur.fetchall()}
        for col in (
            "promotion_id",
            "thought_id",
            "user_id",
            "weight",
            "promoted_at",
            "prov_agent",
        ):
            assert col in rows, f"missing column: {col}"
            assert rows[col] == "NO", f"{col} should be NOT NULL, got {rows[col]}"
        # reason is the only nullable column
        assert "reason" in rows
        assert rows["reason"] == "YES"

    def test_cascade_fk_on_thought_id(self, conn):
        """Promotions must be deleted when their parent thought is forgotten."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT confdeltype FROM pg_constraint
            WHERE conrelid = 'brain.promotions'::regclass
              AND contype = 'f'
              AND confrelid = 'brain.thoughts'::regclass
            """
        )
        row = cur.fetchone()
        assert row is not None, "expected FK on thought_id → brain.thoughts"
        assert row[0] == "c", "thought_id FK should be ON DELETE CASCADE"

    def test_unique_constraint_present(self, conn):
        """UNIQUE (thought_id, user_id, promoted_at) prevents accidental dupes."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE conrelid = 'brain.promotions'::regclass
              AND contype = 'u'
            """
        )
        count = cur.fetchone()[0]
        assert count >= 1, "expected at least one UNIQUE constraint on brain.promotions"

    def test_supporting_indexes_present(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='promotions'
            """
        )
        names = {r[0] for r in cur.fetchall()}
        assert "idx_promotions_thought_user" in names
        assert "idx_promotions_user_time" in names


class TestPromoteBasics:
    def test_promote_succeeds_and_returns_promotion_id(self, conn):
        r = open_brain.capture(conn, text="promote me", user_id="hebbian-basic")
        tid = r["thought_id"]
        try:
            result = open_brain.promote_thought(
                conn, tid, "hebbian-basic", weight=1.0,
            )
            assert result["promotion_id"] > 0
            assert result["thought_id"] == tid
            assert result["weight"] == 1.0
            # Effective weight today is approximately 1.0 (days_since ~ 0).
            assert abs(result["effective_weight"] - 1.0) < 0.01
        finally:
            _cleanup_thought(conn, tid)

    def test_promote_cross_user_rejected(self, conn):
        """PS scoping: cannot promote another user's thought."""
        r = open_brain.capture(conn, text="userA's thought", user_id="hebbian-userA")
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.promote_thought(conn, tid, "hebbian-userB")
        finally:
            _cleanup_thought(conn, tid)

    def test_promote_default_prov_agent(self, conn):
        """prov_agent defaults to cli-user-{user_id} when not supplied."""
        r = open_brain.capture(conn, text="prov default", user_id="hebbian-prov")
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, "hebbian-prov", weight=1.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent FROM brain.promotions WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "cli-user-hebbian-prov"
        finally:
            _cleanup_thought(conn, tid)

    def test_multiple_promotions_accumulate(self, conn):
        r = open_brain.capture(conn, text="multi promote", user_id="hebbian-multi")
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, "hebbian-multi", weight=1.0)
            open_brain.promote_thought(conn, tid, "hebbian-multi", weight=2.0)
            open_brain.promote_thought(conn, tid, "hebbian-multi", weight=0.5)
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-multi")
            # 1.0 + 2.0 + 0.5 = 3.5, decay factor approximately 1.0 today.
            assert abs(eff - 3.5) < 0.05
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM brain.promotions WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == 3
        finally:
            _cleanup_thought(conn, tid)

    def test_promote_with_reason_persists(self, conn):
        r = open_brain.capture(conn, text="rationale test", user_id="hebbian-reason")
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(
                conn, tid, "hebbian-reason",
                weight=1.0, reason="critical decision context",
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT reason FROM brain.promotions WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "critical decision context"
        finally:
            _cleanup_thought(conn, tid)


class TestDemote:
    def test_demote_inserts_negative_weight(self, conn):
        r = open_brain.capture(conn, text="demote test", user_id="hebbian-demote")
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, "hebbian-demote", weight=2.0)
            open_brain.demote_thought(conn, tid, "hebbian-demote", weight=1.0)
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-demote")
            # 2.0 - 1.0 = 1.0 (both promotions effectively undecayed today).
            assert abs(eff - 1.0) < 0.05
            # Both rows exist (audit trail preserved — no deletion).
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM brain.promotions WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == 2
        finally:
            _cleanup_thought(conn, tid)

    def test_demote_can_drive_effective_weight_negative(self, conn):
        """Net demotion below zero is legal — the formula is unbounded."""
        r = open_brain.capture(conn, text="net neg test", user_id="hebbian-neg")
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, "hebbian-neg", weight=1.0)
            open_brain.demote_thought(conn, tid, "hebbian-neg", weight=3.0)
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-neg")
            assert eff < 0
            assert abs(eff - (-2.0)) < 0.05
        finally:
            _cleanup_thought(conn, tid)

    def test_demote_normalizes_positive_input(self, conn):
        """demote_thought(weight=2.0) and demote_thought(weight=-2.0) must
        both insert a negative-weight row. The caller's sign is ignored:
        demote always subtracts."""
        r = open_brain.capture(conn, text="normalize test", user_id="hebbian-norm")
        tid = r["thought_id"]
        try:
            open_brain.demote_thought(conn, tid, "hebbian-norm", weight=2.0)
            open_brain.demote_thought(conn, tid, "hebbian-norm", weight=-3.0)
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-norm")
            # Both demotes counted as negatives: -2.0 + -3.0 = -5.0
            assert abs(eff - (-5.0)) < 0.05
        finally:
            _cleanup_thought(conn, tid)

    def test_demote_cross_user_rejected(self, conn):
        r = open_brain.capture(conn, text="cross-user demote", user_id="hebbian-dx-A")
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.demote_thought(conn, tid, "hebbian-dx-B")
        finally:
            _cleanup_thought(conn, tid)


class TestTimeDecayMath:
    def test_decay_formula_correct_at_1_day(self, conn):
        """At days_since=1: decay = (1+1)^(-0.7) = 2^(-0.7) ~= 0.6156."""
        r = open_brain.capture(conn, text="time decay 1d", user_id="hebbian-decay-1")
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, promoted_at, prov_agent)
                VALUES (%s, %s, 1.0, NOW() - INTERVAL '1 day', 'test')
                """,
                (tid, "hebbian-decay-1"),
            )
            conn.commit()
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-decay-1")
            expected = 2.0 ** (-0.7)  # ~ 0.6156
            assert abs(eff - expected) < 0.02, (
                f"Decay at 1 day: expected ~{expected:.4f}, got {eff:.4f}"
            )
        finally:
            _cleanup_thought(conn, tid)

    def test_decay_formula_correct_at_7_days(self, conn):
        """At days_since=7: decay = (1+7)^(-0.7) = 8^(-0.7) ~= 0.2336."""
        r = open_brain.capture(conn, text="7d decay", user_id="hebbian-decay-7")
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, promoted_at, prov_agent)
                VALUES (%s, %s, 1.0, NOW() - INTERVAL '7 days', 'test')
                """,
                (tid, "hebbian-decay-7"),
            )
            conn.commit()
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-decay-7")
            expected = 8.0 ** (-0.7)  # ~ 0.2336
            assert abs(eff - expected) < 0.02
        finally:
            _cleanup_thought(conn, tid)

    def test_decay_formula_correct_at_30_days(self, conn):
        """At days_since=30: decay = 31^(-0.7) ~= 0.0928. Old promotions fade."""
        r = open_brain.capture(conn, text="30d decay", user_id="hebbian-decay-30")
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, promoted_at, prov_agent)
                VALUES (%s, %s, 1.0, NOW() - INTERVAL '30 days', 'test')
                """,
                (tid, "hebbian-decay-30"),
            )
            conn.commit()
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-decay-30")
            expected = 31.0 ** (-0.7)  # ~ 0.0928
            assert abs(eff - expected) < 0.02
            # Sanity: 30-day-old promotion is *much* weaker than fresh.
            assert eff < 0.15
        finally:
            _cleanup_thought(conn, tid)

    def test_decay_monotonic_decreasing(self, conn):
        """Older promotions must always decay to less than newer ones."""
        r = open_brain.capture(conn, text="monotonic test", user_id="hebbian-mono")
        tid = r["thought_id"]
        try:
            # Insert THREE promotions at different ages, same weight.
            cur = conn.cursor()
            for days in (1, 7, 30):
                cur.execute(
                    """
                    INSERT INTO brain.promotions
                      (thought_id, user_id, weight, promoted_at, prov_agent)
                    VALUES (%s, %s, 1.0, NOW() - INTERVAL %s, 'test')
                    """,
                    (tid, "hebbian-mono", f"{days} days"),
                )
            conn.commit()
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-mono")
            expected = (
                2.0 ** (-0.7) + 8.0 ** (-0.7) + 31.0 ** (-0.7)
            )
            assert abs(eff - expected) < 0.02
        finally:
            _cleanup_thought(conn, tid)

    def test_zero_promotions_returns_zero(self, conn):
        r = open_brain.capture(conn, text="no promotions", user_id="hebbian-zero")
        tid = r["thought_id"]
        try:
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-zero")
            assert eff == 0.0
        finally:
            _cleanup_thought(conn, tid)

    def test_negative_days_since_clamped(self, conn):
        """Clock skew (promoted_at in the future) must not blow up math.
        Implementation clamps days_since to >= 0, so decay caps at 1.0."""
        r = open_brain.capture(conn, text="clock skew", user_id="hebbian-skew")
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, promoted_at, prov_agent)
                VALUES (%s, %s, 2.0, NOW() + INTERVAL '1 hour', 'test')
                """,
                (tid, "hebbian-skew"),
            )
            conn.commit()
            eff = open_brain.compute_effective_weight(conn, tid, "hebbian-skew")
            # days_since clamps to 0, decay = (1+0)^(-0.7) = 1.0, weight=2.0.
            assert abs(eff - 2.0) < 0.05
        finally:
            _cleanup_thought(conn, tid)


class TestCascadeOnForget:
    def test_forget_removes_promotion_rows(self, conn):
        """When a thought is forgotten via VF_eps, its promotions cascade."""
        r = open_brain.capture(
            conn, text="forget cascade test", user_id="hebbian-cascade",
        )
        tid = r["thought_id"]
        open_brain.promote_thought(conn, tid, "hebbian-cascade", weight=2.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM brain.promotions WHERE thought_id=%s",
            (tid,),
        )
        assert cur.fetchone()[0] == 1

        # Invoke VF_eps forget. Small n keeps the test fast; the cascade
        # behavior is unrelated to probe count.
        result = open_brain.forget_thought(
            conn, tid, "hebbian-cascade", epsilon=0.05, n=30,
        )
        # We don't assert status — even on residue-restore, the row
        # comes back and its promotions are gone (FK cascade fired during
        # the DELETE). So this test is robust to either outcome.
        cur.execute(
            "SELECT COUNT(*) FROM brain.promotions WHERE thought_id=%s",
            (tid,),
        )
        assert cur.fetchone()[0] == 0
        # cleanup if restored
        cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
        conn.commit()


class TestCliFlags:
    def test_help_includes_hebbian_flags(self):
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=30,
        )
        assert result.returncode == 0, f"--help failed: {result.stderr}"
        for flag in ("--promote", "--demote", "--weight", "--reason"):
            assert flag in result.stdout, f"{flag} not in --help output"


class TestMultiUserIsolation:
    def test_userA_promotion_invisible_to_userB(self, conn):
        """PS isolation: userA's promotions don't bleed into userB's reads."""
        rA = open_brain.capture(conn, text="userA promote-iso", user_id="hebbian-iso-A")
        rB = open_brain.capture(conn, text="userB promote-iso", user_id="hebbian-iso-B")
        try:
            open_brain.promote_thought(
                conn, rA["thought_id"], "hebbian-iso-A", weight=5.0,
            )
            # userB's effective_weight for their own thought is 0 (no promotions).
            eff_b = open_brain.compute_effective_weight(
                conn, rB["thought_id"], "hebbian-iso-B",
            )
            assert eff_b == 0.0
            # userA's effective_weight is ~5.0
            eff_a = open_brain.compute_effective_weight(
                conn, rA["thought_id"], "hebbian-iso-A",
            )
            assert abs(eff_a - 5.0) < 0.05
        finally:
            _cleanup_thought(conn, rA["thought_id"])
            _cleanup_thought(conn, rB["thought_id"])

    def test_compute_effective_weight_filters_by_user(self, conn):
        """If the same thought_id somehow had promotions under two user_ids
        (defensive — UNIQUE constraint includes user_id), each user's
        compute_effective_weight call returns only their own subset."""
        rA = open_brain.capture(conn, text="userA scope", user_id="hebbian-fs-A")
        tid = rA["thought_id"]
        try:
            # Insert a userA promotion (legal — same user as the thought).
            open_brain.promote_thought(conn, tid, "hebbian-fs-A", weight=3.0)
            # Insert a userB row directly via SQL — bypassing PS to exercise
            # the read-side filter. Demonstrates compute_effective_weight
            # filters by user_id, not just thought_id.
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, prov_agent)
                VALUES (%s, %s, 1.0, 'test')
                """,
                (tid, "hebbian-fs-B"),
            )
            conn.commit()
            eff_a = open_brain.compute_effective_weight(conn, tid, "hebbian-fs-A")
            eff_b = open_brain.compute_effective_weight(conn, tid, "hebbian-fs-B")
            assert abs(eff_a - 3.0) < 0.05
            assert abs(eff_b - 1.0) < 0.05
        finally:
            _cleanup_thought(conn, tid)
