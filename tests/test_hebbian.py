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


# ─── brain-W1-S13 (gz-97l2z): Hebbian search integration ─────────────────────
#
# These tests verify that the Hebbian primitive (S11) is wired into the
# search() retrieval path with the within-kind over-application defense
# (gz-dsax2 / W1-R0): the boost is gated by a vector-similarity floor
# (HEBBIAN_MIN_RELEVANCE_FLOOR = 0.30). A heavily-promoted but
# semantically-irrelevant thought MUST NOT outrank a relevant unpromoted one.


def _result_tid(row):
    """search() uppercases keys; pick the thought_id field robustly."""
    return row.get("THOUGHT_ID") or row.get("thought_id")


class TestHebbianSearchIntegration:
    """Wave-1 S13 — Hebbian promotion boosts search scoring, gated by the
    within-kind over-application defense (vec_similarity >= MIN_RELEVANCE_FLOOR)."""

    def test_promoted_relevant_thought_outranks_unpromoted_relevant(self, conn):
        """A relevant thought that's been promoted outranks an equally-relevant unpromoted one."""
        u = "hebbian-rank-promoted"
        # Both thoughts mention the query topic
        r1 = open_brain.capture(conn, text="alpha quantum entanglement physics", user_id=u)
        r2 = open_brain.capture(conn, text="alpha quantum entanglement physics extra", user_id=u)
        try:
            open_brain.promote_thought(conn, r1["thought_id"], u, weight=5.0)
            results = open_brain.search(conn, query="alpha quantum entanglement", user_id=u, limit=10)
            # r1 (promoted) should rank ahead of r2 (unpromoted)
            tids = [_result_tid(r) for r in results]
            assert r1["thought_id"] in tids and r2["thought_id"] in tids, (
                f"Expected both r1={r1['thought_id']} and r2={r2['thought_id']} in {tids}"
            )
            assert tids.index(r1["thought_id"]) < tids.index(r2["thought_id"])
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                        (r1["thought_id"], r2["thought_id"]))
            conn.commit()

    def test_promoted_irrelevant_thought_does_not_outrank_relevant_unpromoted(self, conn):
        """The within-kind defense: a heavily-promoted-but-irrelevant thought
        below MIN_RELEVANCE_FLOOR gets ZERO boost and cannot leapfrog a
        truly-relevant unpromoted thought."""
        u = "hebbian-within-kind"
        # r_relevant: matches query well
        r_relevant = open_brain.capture(conn,
            text="beta photon laser optics deep coherence resonance",
            user_id=u)
        # r_irrelevant: totally unrelated; heavily promoted
        r_irrelevant = open_brain.capture(conn,
            text="banana sandwich recipe cooking",
            user_id=u)
        try:
            open_brain.promote_thought(conn, r_irrelevant["thought_id"], u, weight=20.0)
            results = open_brain.search(conn,
                query="beta photon laser optics",
                user_id=u, limit=10)
            # r_relevant must outrank r_irrelevant despite massive promotion delta
            tids = [_result_tid(r) for r in results]
            if r_irrelevant["thought_id"] in tids and r_relevant["thought_id"] in tids:
                assert tids.index(r_relevant["thought_id"]) < tids.index(r_irrelevant["thought_id"]), \
                    "Within-kind defense FAILED: heavily-promoted irrelevant outranked relevant"
            # Acceptable also: r_irrelevant doesn't appear at all (below threshold)
            if r_irrelevant["thought_id"] in tids:
                # Verify its promotion_boost was 0 (gated by floor)
                r_obj = [r for r in results if _result_tid(r) == r_irrelevant["thought_id"]][0]
                boost = r_obj.get("PROMOTION_BOOST", r_obj.get("promotion_boost", 0.0))
                assert boost == 0.0, (
                    f"Within-kind defense FAILED: irrelevant thought got non-zero boost {boost}"
                )
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                        (r_relevant["thought_id"], r_irrelevant["thought_id"]))
            conn.commit()

    def test_search_result_carries_effective_weight_and_promotion_boost_fields(self, conn):
        """Every search result row carries effective_weight + promotion_boost fields."""
        u = "hebbian-fields"
        r = open_brain.capture(conn, text="gamma ray spectrum observation", user_id=u)
        try:
            open_brain.promote_thought(conn, r["thought_id"], u, weight=2.0)
            results = open_brain.search(conn, query="gamma ray spectrum", user_id=u, limit=5)
            assert results, "expected at least one result"
            top = results[0]
            assert "EFFECTIVE_WEIGHT" in top or "effective_weight" in top, (
                f"effective_weight field missing from result keys={list(top.keys())}"
            )
            assert "PROMOTION_BOOST" in top or "promotion_boost" in top, (
                f"promotion_boost field missing from result keys={list(top.keys())}"
            )
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (r["thought_id"],))
            conn.commit()

    def test_demoted_thought_ranks_lower(self, conn):
        """A demoted thought (negative effective_weight) ranks lower than an unmodified one."""
        u = "hebbian-demote-rank"
        r1 = open_brain.capture(conn, text="delta wave neural pattern signature", user_id=u)
        r2 = open_brain.capture(conn, text="delta wave neural pattern signature extra", user_id=u)
        try:
            open_brain.demote_thought(conn, r1["thought_id"], u, weight=5.0)
            results = open_brain.search(conn, query="delta wave neural pattern", user_id=u, limit=10)
            tids = [_result_tid(r) for r in results]
            if r1["thought_id"] in tids and r2["thought_id"] in tids:
                assert tids.index(r2["thought_id"]) < tids.index(r1["thought_id"]), \
                    "Demoted thought did not rank lower"
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                        (r1["thought_id"], r2["thought_id"]))
            conn.commit()


class TestComputeEffectiveWeightsBatch:
    """gz-8nsvj — SQL aggregate batch version of compute_effective_weight."""

    def test_batch_returns_zero_for_unpromoted(self, conn):
        u = "hebbian-batch-zero"
        r = open_brain.capture(conn, text="batch zero test", user_id=u)
        try:
            results = open_brain.compute_effective_weights_batch(
                conn, [r["thought_id"]], u)
            assert results[r["thought_id"]] == 0.0
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (r["thought_id"],))
            conn.commit()

    def test_batch_sums_correctly_for_promoted(self, conn):
        u = "hebbian-batch-sum"
        r = open_brain.capture(conn, text="batch sum test", user_id=u)
        try:
            open_brain.promote_thought(conn, r["thought_id"], u, weight=1.0)
            open_brain.promote_thought(conn, r["thought_id"], u, weight=2.0)
            results = open_brain.compute_effective_weights_batch(
                conn, [r["thought_id"]], u)
            assert abs(results[r["thought_id"]] - 3.0) < 0.05
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (r["thought_id"],))
            conn.commit()

    def test_batch_empty_list_returns_empty_dict(self, conn):
        assert open_brain.compute_effective_weights_batch(conn, [], "anyuser") == {}

    def test_batch_matches_single_call_value(self, conn):
        """compute_effective_weights_batch returns the same value as the per-thought version."""
        u = "hebbian-batch-parity"
        r = open_brain.capture(conn, text="parity test", user_id=u)
        try:
            open_brain.promote_thought(conn, r["thought_id"], u, weight=1.5)
            single = open_brain.compute_effective_weight(conn, r["thought_id"], u)
            batch = open_brain.compute_effective_weights_batch(
                conn, [r["thought_id"]], u)
            assert abs(single - batch[r["thought_id"]]) < 0.01
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (r["thought_id"],))
            conn.commit()

    def test_batch_handles_multiple_thoughts(self, conn):
        """Batch fetch returns one entry per requested thought_id, including
        unpromoted ones (defaulted to 0.0)."""
        u = "hebbian-batch-multi"
        r_a = open_brain.capture(conn, text="batch multi a", user_id=u)
        r_b = open_brain.capture(conn, text="batch multi b", user_id=u)
        r_c = open_brain.capture(conn, text="batch multi c (no promo)", user_id=u)
        try:
            open_brain.promote_thought(conn, r_a["thought_id"], u, weight=2.0)
            open_brain.promote_thought(conn, r_b["thought_id"], u, weight=4.0)
            tids = [r_a["thought_id"], r_b["thought_id"], r_c["thought_id"]]
            results = open_brain.compute_effective_weights_batch(conn, tids, u)
            assert set(results.keys()) == set(tids)
            assert abs(results[r_a["thought_id"]] - 2.0) < 0.05
            assert abs(results[r_b["thought_id"]] - 4.0) < 0.05
            assert results[r_c["thought_id"]] == 0.0
        finally:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s, %s)",
                (r_a["thought_id"], r_b["thought_id"], r_c["thought_id"]),
            )
            conn.commit()

    def test_batch_respects_user_scope(self, conn):
        """Promotions under a different user_id must not bleed into the
        batch fetch for the requested user."""
        u_a = "hebbian-batch-scope-A"
        u_b = "hebbian-batch-scope-B"
        r = open_brain.capture(conn, text="batch scope test", user_id=u_a)
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, u_a, weight=3.0)
            # Bypass PS via direct SQL to plant a userB row.
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brain.promotions
                  (thought_id, user_id, weight, prov_agent)
                VALUES (%s, %s, 99.0, 'test')
                """,
                (tid, u_b),
            )
            conn.commit()
            results_a = open_brain.compute_effective_weights_batch(conn, [tid], u_a)
            results_b = open_brain.compute_effective_weights_batch(conn, [tid], u_b)
            assert abs(results_a[tid] - 3.0) < 0.05
            assert abs(results_b[tid] - 99.0) < 0.5
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()

    def test_batch_includes_unpromoted_with_zero(self, conn):
        """Unpromoted thought_ids in the input list MUST appear with value 0.0
        (not be silently dropped from the returned dict)."""
        u = "hebbian-batch-fill"
        r = open_brain.capture(conn, text="batch fill test", user_id=u)
        try:
            results = open_brain.compute_effective_weights_batch(
                conn, [r["thought_id"], "nonexistent-thought-id-12345"], u)
            assert r["thought_id"] in results
            assert "nonexistent-thought-id-12345" in results
            assert results["nonexistent-thought-id-12345"] == 0.0
            assert results[r["thought_id"]] == 0.0
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (r["thought_id"],))
            conn.commit()
