"""brain-W1-S7: VF_eps probe library tests.

Verifies the load-bearing dual-bound math (Hoeffding loose + exact binomial
tight), probe distribution arithmetic, snapshot construction, and the
accept-iff-k=0 contract.

The 99.9999793% exact-binomial confidence figure at n=300/k=0/eps=0.05 IS the
procurement headline — these tests anchor it.

Run: python3 -m pytest tests/test_vf_probe.py -v
"""
import os
import sys
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


def _cleanup_thought(conn, tid):
    """Best-effort cleanup: knowledge-graph artefacts first, then the thought.

    Each delete is wrapped so a missing-table or constraint issue in one
    table does not prevent cleanup of the others.
    """
    cur = conn.cursor()
    try:
        for sql, params in (
            (
                "DELETE FROM brain.knowledge_graph_edges "
                "WHERE source_thought_id = %s OR target_thought_id = %s",
                (tid, tid),
            ),
            (
                "DELETE FROM brain.knowledge_graph_nodes WHERE thought_id = %s",
                (tid,),
            ),
            (
                "DELETE FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            ),
        ):
            try:
                cur.execute(sql, params)
                conn.commit()
            except Exception:
                conn.rollback()
    finally:
        cur.close()


# ─── Hoeffding + exact binomial bound math ──────────────────────────────────


class TestBoundMath:
    """Lin/Li/Chen §12.1 dual-bound math — load-bearing arithmetic."""

    def test_hoeffding_at_n300_eps005(self):
        """Hoeffding bound at the procurement parameters: 0.2231 (loose)."""
        b = vf_probe.hoeffding_bound(300, 0.05)
        assert abs(b - 0.2231) < 1e-3, (
            f"Hoeffding bound at n=300/eps=0.05 should be ~0.2231, got {b}"
        )

    def test_hoeffding_confidence_at_n300_eps005(self):
        """Hoeffding confidence: 77.69% (loose)."""
        b = vf_probe.hoeffding_bound(300, 0.05)
        c = vf_probe.confidence(b)
        assert abs(c - 0.7769) < 1e-3, (
            f"Hoeffding confidence at n=300/eps=0.05 should be ~0.7769, got {c}"
        )

    def test_exact_binomial_at_n300_eps005(self):
        """Exact binomial bound: 2.075e-7 (tight)."""
        b = vf_probe.exact_binomial_bound(300, 0.05)
        assert abs(b - 2.075e-7) < 1e-8, (
            f"Exact binomial at n=300/eps=0.05 should be ~2.075e-7, got {b}"
        )

    def test_exact_binomial_confidence_procurement_figure(self):
        """The procurement headline: 99.9999793% confidence."""
        b = vf_probe.exact_binomial_bound(300, 0.05)
        c = vf_probe.confidence(b)
        # The procurement claim is "99.9999793%" = 0.999_999_79 to 8 decimals.
        assert abs(c - 0.999_999_79) < 1e-6, (
            "Exact-binomial confidence at n=300/k=0/eps=0.05 should be "
            f"~99.9999793%, got {c}"
        )

    def test_hoeffding_loose_vs_binomial_tight(self):
        """Hoeffding gives a LOOSER (higher) bound than exact binomial."""
        n, eps = 300, 0.05
        hb = vf_probe.hoeffding_bound(n, eps)
        ebb = vf_probe.exact_binomial_bound(n, eps)
        assert hb > ebb, "Hoeffding should be looser than exact binomial"

    def test_increasing_n_decreases_both_bounds(self):
        """Both bounds shrink monotonically as n grows (for fixed eps)."""
        for eps in (0.05, 0.10):
            for n_lo, n_hi in [(100, 200), (200, 300), (300, 500)]:
                assert (
                    vf_probe.hoeffding_bound(n_hi, eps)
                    < vf_probe.hoeffding_bound(n_lo, eps)
                )
                assert (
                    vf_probe.exact_binomial_bound(n_hi, eps)
                    < vf_probe.exact_binomial_bound(n_lo, eps)
                )


# ─── ProbeSeedSnapshot pre-delete capture ───────────────────────────────────


class TestSeedSnapshot:
    """ProbeSeedSnapshot captures pre-delete state correctly."""

    def test_snapshot_for_existing_thought_succeeds(self, conn):
        r = open_brain.capture(
            conn, text="thought to forget", user_id="vf-snap-user"
        )
        tid = r["thought_id"]
        try:
            snap = vf_probe.build_probe_seed_snapshot(
                conn, tid, "vf-snap-user"
            )
            assert snap.forgotten_thought_id == tid
            assert "thought to forget" in snap.forgotten_text
            assert snap.user_id == "vf-snap-user"
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_cross_user_rejected(self, conn):
        """PS — Principal Scoping: snapshot rejects cross-user access."""
        r = open_brain.capture(
            conn, text="userA thought", user_id="vf-snap-userA"
        )
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                vf_probe.build_probe_seed_snapshot(
                    conn, tid, "vf-snap-userB"
                )
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_missing_thought_raises(self, conn):
        with pytest.raises(RuntimeError, match="not in user scope"):
            vf_probe.build_probe_seed_snapshot(
                conn, "brain-nonexistent-0001", "any-user"
            )


# ─── Probe generation per kind ──────────────────────────────────────────────


class TestProbeGeneration:
    """Probe generators emit the exact requested counts and reasonable content."""

    def _fake_snapshot(self):
        return vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="brain-test-fake-001",
            forgotten_text=(
                "The quick brown fox jumps over the lazy dog. "
                "This is a test thought."
            ),
            forgotten_summary="test summary",
            forgotten_embedding=[0.1] * 768,
            neighbors_sexprs=[
                "neighbor 1 text",
                "neighbor 2 text",
                "neighbor 3 text",
            ],
            user_id="testuser",
        )

    def test_semantic_probes_count_matches_request(self):
        snap = self._fake_snapshot()
        probes = vf_probe._generate_semantic_probes(snap, 5)
        assert len(probes) == 5
        # Should rotate through the 3 neighbors (wraps deterministically)
        assert probes[0] == "neighbor 1 text"
        assert probes[3] == "neighbor 1 text"  # wraps

    def test_semantic_probes_no_neighbors_falls_back_to_forgotten_text(self):
        snap = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="brain-test-noneigh-001",
            forgotten_text="only-the-forgotten-text",
            forgotten_summary=None,
            forgotten_embedding=[0.0] * 768,
            neighbors_sexprs=[],
            user_id="testuser",
        )
        probes = vf_probe._generate_semantic_probes(snap, 3)
        assert len(probes) == 3
        for p in probes:
            assert p == "only-the-forgotten-text"

    def test_partial_probes_count_matches_request(self):
        snap = self._fake_snapshot()
        probes = vf_probe._generate_partial_probes(snap, 7)
        assert len(probes) == 7
        # All probes should be substrings of forgotten_text
        for p in probes:
            assert p in snap.forgotten_text

    def test_perturb_probes_preserve_dimension(self):
        snap = self._fake_snapshot()
        probes = vf_probe._generate_perturb_probes(snap, 4)
        assert len(probes) == 4
        for v in probes:
            assert len(v) == 768

    def test_paraphrase_falls_back_to_partial_when_no_api_key(self, monkeypatch):
        """No ANTHROPIC_API_KEY → paraphrase probes fall back to partial fragments."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        snap = self._fake_snapshot()
        probes = vf_probe._generate_paraphrase_probes(snap, 5)
        assert len(probes) == 5
        # Should be substrings (fallback behavior)
        for p in probes:
            assert p in snap.forgotten_text


# ─── End-to-end verify_forgetting accept/reject ─────────────────────────────


class TestVerifyForgetting:
    """The end-to-end accept/reject contract under k=0 vs k>0."""

    def test_verify_accepts_when_live_row_deleted(self, conn, monkeypatch):
        """After deleting the live row, all probes return k=0 → accepted=True."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        r = open_brain.capture(
            conn,
            text="content to forget completely",
            user_id="vf-verify-accept",
        )
        tid = r["thought_id"]
        snap = vf_probe.build_probe_seed_snapshot(
            conn, tid, "vf-verify-accept"
        )
        # Delete the row (simulating S8's delete step)
        _cleanup_thought(conn, tid)
        # Now verify — should accept with k=0
        result = vf_probe.verify_forgetting(conn, snap, n=30)  # n=30 for speed
        assert result.accepted is True
        assert result.k == 0
        assert result.n == 30
        assert result.probeQuality["sampledFromSnapshot"] is True

    def test_verify_rejects_when_live_row_still_exists(self, conn, monkeypatch):
        """Live row NOT deleted → probes trivially find it → accepted=False."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        r = open_brain.capture(
            conn,
            text="content still live and discoverable",
            user_id="vf-verify-reject",
        )
        tid = r["thought_id"]
        try:
            snap = vf_probe.build_probe_seed_snapshot(
                conn, tid, "vf-verify-reject"
            )
            # NOTE: row NOT deleted here
            result = vf_probe.verify_forgetting(conn, snap, n=30)
            assert result.accepted is False
            assert result.k > 0
        finally:
            _cleanup_thought(conn, tid)

    def test_verify_records_both_bounds(self, conn, monkeypatch):
        """At n=300/eps=0.05, both bounds match the procurement values."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        r = open_brain.capture(
            conn, text="bound recording test", user_id="vf-verify-bounds"
        )
        tid = r["thought_id"]
        snap = vf_probe.build_probe_seed_snapshot(
            conn, tid, "vf-verify-bounds"
        )
        _cleanup_thought(conn, tid)
        result = vf_probe.verify_forgetting(
            conn, snap, n=300, epsilon=0.05
        )
        # Both bounds present + at the procurement values
        assert abs(result.hoeffdingBound - 0.2231) < 1e-3
        assert abs(result.hoeffdingConfidence - 0.7769) < 1e-3
        assert abs(result.exactBinomialBound - 2.075e-7) < 1e-8
        assert abs(result.exactBinomialConfidence - 0.999_999_79) < 1e-6

    def test_verify_distribution_sums_to_n(self, conn, monkeypatch):
        """Probe distribution at default → sum equals n."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        r = open_brain.capture(
            conn, text="dist test", user_id="vf-verify-dist"
        )
        tid = r["thought_id"]
        snap = vf_probe.build_probe_seed_snapshot(
            conn, tid, "vf-verify-dist"
        )
        _cleanup_thought(conn, tid)
        result = vf_probe.verify_forgetting(conn, snap, n=300)
        dist = result.probeQuality["distribution"]
        assert sum(dist.values()) == 300, (
            f"Distribution sums to {sum(dist.values())}, expected 300"
        )

    def test_verify_default_n_is_300(self):
        assert vf_probe.DEFAULT_N == 300

    def test_verify_default_epsilon_is_005(self):
        assert vf_probe.DEFAULT_EPSILON == 0.05
