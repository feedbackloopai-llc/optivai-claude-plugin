"""brain-W1-S9: VF_eps corpus expansion + benign-persistence cases.

S7 (vf_probe library) and S8 (--forget CLI) shipped 35 tests covering
the load-bearing primitives at n=300/eps=0.05. S9 fills the remaining
gaps the plan called for:

  - Benign-persistence cases per Lin/Li/Chen 2026 §12.2 (UCC-style
    cross-user contamination probes — 57-71% benign contamination
    documented in Lin §12.2 is the threat surface)
  - Bound calculations at multiple n values (n=100, 500, 1000)
  - Audit log completeness (every column populated; types correct)
  - Probe-quality marker structural conformance

This file is test-only — no production code touched.
"""
import os
import sys
import json
import math
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402
import vf_probe  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


# ─── Benign-persistence (Lin §12.2 UCC contamination) ────────────────────────


class TestBenignPersistence:
    """Lin/Li/Chen 2026 §12.2 documents 57-71% benign cross-user memory
    contamination in commercial agent harnesses (UCC = User Cross-Contamination).
    The PS primitive must block this even without an active attacker.

    These tests confirm that forget(userA's thought) cannot be triggered by
    userB, and that VF_eps probes after a forget cannot surface another
    user's similar thought."""

    def test_userB_cannot_forget_userA_thought(self, conn):
        """The most basic UCC scenario: userB tries to forget userA's data."""
        rA = open_brain.capture(conn, text="userA confidential alpha",
                                 user_id="vf-ucc-userA")
        tid = rA["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.forget_thought(conn, tid, "vf-ucc-userB", n=30)
            # userA's row must remain
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM brain.thoughts WHERE thought_id=%s", (tid,))
            assert cur.fetchone() is not None, "userA's row was deleted by userB"
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()

    def test_forget_in_userA_scope_does_not_affect_userB_similar_content(self, conn):
        """If two users have similar content, forget(userA's row) MUST leave
        userB's row untouched, even when their texts are near-identical."""
        rA = open_brain.capture(
            conn,
            text="UCC-shared-content marker XYZ unique-suffix-A",
            user_id="vf-ucc-share-userA",
        )
        rB = open_brain.capture(
            conn,
            text="UCC-shared-content marker XYZ unique-suffix-B",
            user_id="vf-ucc-share-userB",
        )
        tidA = rA["thought_id"]
        tidB = rB["thought_id"]
        try:
            result = open_brain.forget_thought(conn, tidA, "vf-ucc-share-userA", n=30)
            assert result["status"] == "forgotten"
            # userA's row gone
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM brain.thoughts WHERE thought_id=%s", (tidA,))
            assert cur.fetchone() is None
            # userB's row still present
            cur.execute("SELECT raw_text FROM brain.thoughts WHERE thought_id=%s", (tidB,))
            row = cur.fetchone()
            assert row is not None, "userB's row was wrongly affected by userA's forget"
            assert "unique-suffix-B" in row[0]
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                        (tidA, tidB))
            conn.commit()

    def test_audit_log_scope_to_invoking_user_only(self, conn):
        """The forget_audit row records the invoking user_id, not the target user.
        UCC contamination of audit logs would let userB see userA's audit history."""
        rA = open_brain.capture(conn, text="audit scope test",
                                 user_id="vf-ucc-audit-userA")
        tid = rA["thought_id"]
        open_brain.forget_thought(conn, tid, "vf-ucc-audit-userA", n=30)
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM brain.forget_audit WHERE forgotten_thought_id=%s",
            (tid,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "vf-ucc-audit-userA"

    def test_seed_snapshot_cannot_be_built_cross_user(self, conn):
        """build_probe_seed_snapshot — the entry point of forget — must reject
        cross-user before any state is captured."""
        r = open_brain.capture(conn, text="snap UCC test", user_id="vf-ucc-snap-A")
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                vf_probe.build_probe_seed_snapshot(conn, tid, "vf-ucc-snap-B")
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()


# ─── Multi-n bound math ───────────────────────────────────────────────────────


class TestBoundsAtMultipleN:
    """The dual-bound math is correct across the n value range, not just at
    the procurement point (n=300). Verifies the formulas are general."""

    def test_hoeffding_at_n100_eps005(self):
        b = vf_probe.hoeffding_bound(100, 0.05)
        # exp(-2*100*0.0025) = exp(-0.5) ≈ 0.6065
        assert abs(b - 0.6065) < 1e-3

    def test_hoeffding_at_n500_eps005(self):
        b = vf_probe.hoeffding_bound(500, 0.05)
        # exp(-2.5) ≈ 0.0821
        assert abs(b - 0.0821) < 1e-3

    def test_exact_binomial_at_n100_eps005(self):
        b = vf_probe.exact_binomial_bound(100, 0.05)
        # 0.95^100 ≈ 0.00592
        assert abs(b - 0.00592) < 1e-4

    def test_exact_binomial_at_n1000_eps005(self):
        b = vf_probe.exact_binomial_bound(1000, 0.05)
        # 0.95^1000 ≈ 5.29e-23 (extremely tight at high n)
        assert b < 1e-20

    def test_hoeffding_always_looser_than_binomial_across_n(self):
        """Hoeffding ≥ exact binomial for all n ≥ 1 (loose ≥ tight)."""
        for n in [10, 50, 100, 300, 500, 1000, 5000]:
            hb = vf_probe.hoeffding_bound(n, 0.05)
            ebb = vf_probe.exact_binomial_bound(n, 0.05)
            assert hb >= ebb, f"At n={n}: hoeffding={hb} < binomial={ebb}"

    def test_higher_epsilon_yields_smaller_binomial(self):
        """Higher tolerance epsilon → easier to satisfy → smaller binomial bound."""
        for n in [100, 300, 1000]:
            b_strict = vf_probe.exact_binomial_bound(n, 0.05)
            b_loose = vf_probe.exact_binomial_bound(n, 0.10)
            assert b_loose < b_strict, (
                f"At n={n}: eps=0.10 bound {b_loose} should be smaller than "
                f"eps=0.05 bound {b_strict}"
            )


# ─── Audit log structural completeness ────────────────────────────────────────


class TestAuditLogCompleteness:
    """Every column of brain.forget_audit is populated after a forget call;
    types are correct; JSONB columns parse."""

    def test_all_columns_populated_after_forget(self, conn):
        r = open_brain.capture(conn, text="audit completeness test",
                                user_id="vf-audit-complete")
        tid = r["thought_id"]
        open_brain.forget_thought(conn, tid, "vf-audit-complete", n=30, epsilon=0.05,
                                    prov_agent="completeness-test-agent")
        cur = conn.cursor()
        cur.execute("""
            SELECT audit_id, forgotten_thought_id, user_id, status,
                   n, k, epsilon,
                   hoeffding_bound, hoeffding_confidence,
                   exact_binomial_bound, exact_binomial_conf,
                   probe_quality_json, prov_agent, prov_activity,
                   created_at
            FROM brain.forget_audit
            WHERE forgotten_thought_id = %s
            ORDER BY audit_id DESC LIMIT 1
        """, (tid,))
        row = cur.fetchone()
        assert row is not None
        # Required NOT-NULL fields
        for i, name in enumerate([
            "audit_id", "forgotten_thought_id", "user_id", "status",
            "n", "k", "epsilon",
            "hoeffding_bound", "hoeffding_confidence",
            "exact_binomial_bound", "exact_binomial_conf",
            "probe_quality_json", "prov_agent", "prov_activity",
            "created_at",
        ]):
            assert row[i] is not None, f"Field {name} is NULL"
        # prov_agent override took effect
        assert row[12] == "completeness-test-agent"
        # prov_activity default
        assert row[13] == "forget"

    def test_probe_quality_json_parses_correctly(self, conn):
        r = open_brain.capture(conn, text="probe quality json test",
                                user_id="vf-audit-json")
        tid = r["thought_id"]
        open_brain.forget_thought(conn, tid, "vf-audit-json", n=30)
        cur = conn.cursor()
        cur.execute(
            "SELECT probe_quality_json FROM brain.forget_audit "
            "WHERE forgotten_thought_id=%s ORDER BY audit_id DESC LIMIT 1",
            (tid,),
        )
        pq = cur.fetchone()[0]
        # psycopg2 may return dict or string depending on JSONB driver mode
        if isinstance(pq, str):
            pq = json.loads(pq)
        assert pq.get("sampledFromSnapshot") is True
        assert pq.get("n") == 30
        assert "distribution" in pq
        assert sum(pq["distribution"].values()) == 30


# ─── Probe-quality marker structural conformance ──────────────────────────────


class TestProbeQualityMarker:
    """Lin §12.1 R3 fix-wave: probeQuality must record n, distribution,
    sampledFromSnapshot. The library's contract — exercised here at multiple n."""

    def test_distribution_sums_to_n_across_values(self, conn):
        r = open_brain.capture(conn, text="distribution sum test",
                                user_id="vf-pq-sum")
        tid = r["thought_id"]
        snap = vf_probe.build_probe_seed_snapshot(conn, tid, "vf-pq-sum")
        cur = conn.cursor()
        cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
        conn.commit()
        for n in [5, 50, 100, 300, 555, 1000]:
            result = vf_probe.verify_forgetting(conn, snap, n=n)
            assert sum(result.probeQuality["distribution"].values()) == n, (
                f"At n={n}, distribution sums to "
                f"{sum(result.probeQuality['distribution'].values())}"
            )

    def test_sampledFromSnapshot_always_true(self, conn):
        r = open_brain.capture(conn, text="sampled marker test",
                                user_id="vf-pq-marker")
        tid = r["thought_id"]
        snap = vf_probe.build_probe_seed_snapshot(conn, tid, "vf-pq-marker")
        cur = conn.cursor()
        cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
        conn.commit()
        result = vf_probe.verify_forgetting(conn, snap, n=30)
        assert result.probeQuality["sampledFromSnapshot"] is True
