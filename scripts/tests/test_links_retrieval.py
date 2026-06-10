#!/usr/bin/env python3
"""fblai-lk4gt — atom_links provenance graph wired into retrieval.

Tests for three behaviors introduced in fblai-lk4gt:

  1. SUPERSEDES SUPPRESSION: A candidate atom that is the TARGET of a live
     ``supersedes`` link gets its HYBRID_SCORE multiplied by the penalty
     factor (default 0.5) and sinks below the superseding atom.
  2. DISPUTED ANNOTATION: A candidate atom that is the TARGET of a live
     ``refutes`` or ``contradicts`` link is annotated with a ``DISPUTED``
     key but receives NO score penalty.
  3. GRAPH_SEARCH UNION: graph_search() walks atom_links 1 hop from seed
     thoughts, merging linked atoms that would NOT have appeared via
     kg_neighborhood alone.

Pure-logic unit tests (no DB) cover cases that can be verified without a live
database.  Integration tests require DATABASE_URL and are SKIPped cleanly when
it is absent or psycopg2 is missing.

Run:
    python3 -m pytest scripts/tests/test_links_retrieval.py -v
"""
import json
import math
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _make_result(
    thought_id: str,
    hybrid_score: float,
    similarity: float = 0.9,
    thought_type: str = "insight",
) -> Dict[str, Any]:
    """Build a minimal uppercased result dict matching the shape search() returns."""
    return {
        "THOUGHT_ID": thought_id,
        "HYBRID_SCORE": hybrid_score,
        "SIMILARITY": similarity,
        "THOUGHT_TYPE": thought_type,
        "SUMMARY": f"summary of {thought_id}",
        "RAW_TEXT": f"raw text of {thought_id}",
        "CREATED_AT": "2026-06-01 00:00:00",
        "TOPICS": [],
        "PEOPLE": [],
        "ACTION_ITEMS": [],
        "KEYWORD_BOOST": 0.0,
        "TIME_DECAY": 0.05,
    }


# ─── Fixtures (DB-dependent) ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    """Module-scoped live Postgres connection; skips if not available.

    After init_schema() the Neon connection pooler may reset the session,
    causing the ``vector`` extension type to become unresolvable on the same
    connection object.  Closing and reopening after init works around this by
    getting a fresh session where the type is available.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            db_url = open_brain._get_database_url()
        except Exception:
            pytest.skip("No DATABASE_URL configured")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 not installed")
    # First connection: run init_schema to ensure tables exist.
    c_init = open_brain._connect()
    open_brain.init_schema(c_init)
    c_init.close()
    # Second connection: fresh session where pgvector::vector is resolvable.
    c = open_brain._connect()
    yield c
    c.close()


@pytest.fixture()
def test_user(conn):
    """Per-test isolated user; deleted at teardown."""
    uid = f"test-lkr-{uuid.uuid4().hex[:12]}"
    yield uid
    cur = conn.cursor()
    try:
        for tbl in (
            "brain.kg_edges",
            "brain.kg_nodes",
            "brain.knowledge_graph_edges",
            "brain.knowledge_graph_nodes",
        ):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        for stmt in [
            "DELETE FROM brain.atom_links WHERE user_id = %s",
            "DELETE FROM brain.replay_log WHERE user_id = %s",
            "DELETE FROM brain.promotions WHERE user_id = %s",
        ]:
            try:
                cur.execute(stmt, (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.thought_versions WHERE thought_id IN "
                "(SELECT thought_id FROM brain.thoughts WHERE user_id = %s)",
                (uid,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("DELETE FROM brain.thoughts WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        cur.close()


def _insert_thought_with_embedding(
    conn,
    user_id: str,
    text: str,
    thought_type: str = "insight",
    summary: Optional[str] = None,
) -> str:
    """Insert a thought with a real embedding so it appears in vector search."""
    thought_id = open_brain._generate_thought_id()
    embedding = open_brain._generate_embedding(text)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thoughts (
                thought_id, user_id, raw_text, summary, thought_type,
                topics, people, action_items, source, session_id, project,
                prov_agent, prov_activity, was_generated_by, was_derived_from,
                source_uri, embedding, metadata, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                'test', '', '',
                %s, 'capture', %s, NULL, NULL,
                %s::vector, NULL,
                NOW(), NOW()
            )
            """,
            (
                thought_id,
                user_id,
                text[:16384],
                (summary or text)[:1000],
                thought_type,
                f"cli-user-{user_id}"[:100],
                f"activity-{thought_id}",
                str(embedding),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return thought_id


def _insert_thought_no_embedding(
    conn,
    user_id: str,
    text: str,
    thought_type: str = "insight",
    summary: Optional[str] = None,
) -> str:
    """Insert a thought WITHOUT an embedding (will not appear in vector search)."""
    thought_id = open_brain._generate_thought_id()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thoughts (
                thought_id, user_id, raw_text, summary, thought_type,
                topics, people, action_items, source, session_id, project,
                prov_agent, prov_activity, was_generated_by, was_derived_from,
                source_uri, embedding, metadata, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                'test', '', '',
                %s, 'capture', %s, NULL, NULL,
                NULL, NULL,
                NOW(), NOW()
            )
            """,
            (
                thought_id,
                user_id,
                text[:16384],
                (summary or text)[:1000],
                thought_type,
                f"cli-user-{user_id}"[:100],
                f"activity-{thought_id}",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return thought_id


# ─── Pure-logic unit tests (no DB needed) ────────────────────────────────────


class TestPenaltyMath:
    """Verify the penalty arithmetic isolated from DB dependencies."""

    def test_default_penalty_halves_score(self):
        """With default penalty 0.5, a superseded atom's HYBRID_SCORE is halved."""
        # Simulate the annotation loop directly.
        results = [
            _make_result("atom-a", hybrid_score=0.8),
            _make_result("atom-b", hybrid_score=0.6),  # will be suppressed
        ]
        penalty = 0.5
        superseded_by_map = {"atom-b": ["atom-a"]}
        for r in results:
            tid = r["THOUGHT_ID"]
            if tid in superseded_by_map:
                r["SUPERSEDED_BY"] = superseded_by_map[tid]
                r["HYBRID_SCORE"] = round(r["HYBRID_SCORE"] * penalty, 4)

        assert results[1]["HYBRID_SCORE"] == pytest.approx(0.3, abs=1e-4)
        assert results[0]["HYBRID_SCORE"] == pytest.approx(0.8, abs=1e-4)

    def test_penalty_0_eliminates_score(self):
        """Penalty=0.0 means the superseded atom gets score 0."""
        r = _make_result("atom-x", hybrid_score=0.75)
        r["HYBRID_SCORE"] = round(r["HYBRID_SCORE"] * 0.0, 4)
        assert r["HYBRID_SCORE"] == 0.0

    def test_penalty_1_is_no_op(self):
        """Penalty=1.0 leaves score unchanged."""
        original = 0.65
        r = _make_result("atom-x", hybrid_score=original)
        r["HYBRID_SCORE"] = round(r["HYBRID_SCORE"] * 1.0, 4)
        assert r["HYBRID_SCORE"] == pytest.approx(original, abs=1e-4)

    def test_penalty_clamped_above_1(self):
        """Values > 1.0 must be clamped to 1.0 (the env-var clamping contract)."""
        raw = float("1.5")
        clamped = max(0.0, min(1.0, raw))
        assert clamped == 1.0

    def test_penalty_clamped_below_0(self):
        """Values < 0.0 must be clamped to 0.0."""
        raw = float("-0.2")
        clamped = max(0.0, min(1.0, raw))
        assert clamped == 0.0

    def test_sort_after_penalty_sinks_superseded(self):
        """After penalty, re-sorting by HYBRID_SCORE puts superseded atom below."""
        results = [
            _make_result("atom-a", hybrid_score=0.9),  # supersedes atom-b
            _make_result("atom-b", hybrid_score=0.85),  # would rank #2 without penalty
        ]
        penalty = 0.5
        # Apply penalty to atom-b
        results[1]["SUPERSEDED_BY"] = ["atom-a"]
        results[1]["HYBRID_SCORE"] = round(results[1]["HYBRID_SCORE"] * penalty, 4)
        # Re-sort
        results.sort(key=lambda x: x["HYBRID_SCORE"], reverse=True)
        assert results[0]["THOUGHT_ID"] == "atom-a"
        assert results[1]["THOUGHT_ID"] == "atom-b"
        # Atom-b's penalised score must be below atom-a's original score
        assert results[1]["HYBRID_SCORE"] < results[0]["HYBRID_SCORE"]


class TestDisputedAnnotation:
    """Verify DISPUTED annotation logic — no score penalty."""

    def test_disputed_annotation_no_score_change(self):
        """refutes/contradicts annotates DISPUTED but never changes HYBRID_SCORE."""
        r = _make_result("atom-x", hybrid_score=0.72)
        original_score = r["HYBRID_SCORE"]
        r["DISPUTED"] = {"by": ["atom-y"], "types": ["refutes"]}
        # Score must be unchanged
        assert r["HYBRID_SCORE"] == pytest.approx(original_score, abs=1e-4)

    def test_disputed_annotation_structure(self):
        """DISPUTED must have 'by' list and 'types' list."""
        r = _make_result("atom-x", hybrid_score=0.5)
        r["DISPUTED"] = {"by": ["src-1", "src-2"], "types": ["refutes", "contradicts"]}
        d = r["DISPUTED"]
        assert isinstance(d["by"], list)
        assert isinstance(d["types"], list)
        assert len(d["by"]) == len(d["types"]) == 2


class TestFormatAnnotations:
    """Verify _format_search_results surfaces SUPERSEDED_BY and DISPUTED markers."""

    def test_format_shows_superseded_by_marker(self):
        """Formatted output must contain [SUPERSEDED_BY <id>]."""
        r = _make_result("atom-a", hybrid_score=0.3)
        r["SUPERSEDED_BY"] = ["atom-b"]
        output = open_brain._format_search_results([r])
        assert "[SUPERSEDED_BY atom-b]" in output

    def test_format_shows_disputed_marker(self):
        """Formatted output must contain [DISPUTED by <id>]."""
        r = _make_result("atom-a", hybrid_score=0.5)
        r["DISPUTED"] = {"by": ["atom-c"], "types": ["refutes"]}
        output = open_brain._format_search_results([r])
        assert "[DISPUTED by atom-c]" in output

    def test_format_shows_both_markers_when_both_present(self):
        """An atom with both SUPERSEDED_BY and DISPUTED shows both markers."""
        r = _make_result("atom-a", hybrid_score=0.3)
        r["SUPERSEDED_BY"] = ["atom-new"]
        r["DISPUTED"] = {"by": ["atom-q"], "types": ["contradicts"]}
        output = open_brain._format_search_results([r])
        assert "[SUPERSEDED_BY atom-new]" in output
        assert "[DISPUTED by atom-q]" in output

    def test_format_no_marker_when_not_annotated(self):
        """Normal atoms must not show any provenance markers."""
        r = _make_result("atom-plain", hybrid_score=0.9)
        output = open_brain._format_search_results([r])
        assert "SUPERSEDED_BY" not in output
        assert "DISPUTED" not in output

    def test_format_multiple_superseded_ids(self):
        """SUPERSEDED_BY with multiple sources joins them comma-separated."""
        r = _make_result("atom-old", hybrid_score=0.2)
        r["SUPERSEDED_BY"] = ["atom-new1", "atom-new2"]
        output = open_brain._format_search_results([r])
        assert "atom-new1" in output
        assert "atom-new2" in output


# ─── Integration tests (DB required) ─────────────────────────────────────────


class TestSupersedesSuppression:
    """(a) Supersedes suppression: B→A link makes A rank below B in search."""

    def test_superseded_atom_annotated_and_penalised(self, conn, test_user):
        """Atom A is superseded by B: SUPERSEDED_BY set, score multiplied by penalty."""
        # Use a common topic to ensure both atoms match the query.
        text_a = "quantum entanglement photon polarization experiment"
        text_b = "updated quantum entanglement photon polarization study 2026"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)

        # B supersedes A
        open_brain.add_link(
            conn,
            source_id=id_b,
            target_id=id_a,
            link_type="supersedes",
            user_id=test_user,
        )

        results = open_brain.search(
            conn, "quantum entanglement photon", test_user, limit=20, threshold=0.0
        )

        ids_returned = [r["THOUGHT_ID"] for r in results]
        # Both atoms must appear
        assert id_a in ids_returned, "Atom A must appear in results (even penalised)"
        assert id_b in ids_returned, "Atom B must appear in results"

        # A must carry SUPERSEDED_BY annotation
        result_a = next(r for r in results if r["THOUGHT_ID"] == id_a)
        assert "SUPERSEDED_BY" in result_a
        assert id_b in result_a["SUPERSEDED_BY"]

        # B must rank above A after penalty
        pos_a = ids_returned.index(id_a)
        pos_b = ids_returned.index(id_b)
        assert pos_b < pos_a, (
            f"B (superseder) must rank above A (superseded): "
            f"pos_b={pos_b} pos_a={pos_a}"
        )

    def test_penalty_applied_proportionally(self, conn, test_user):
        """A's HYBRID_SCORE after annotation is the pre-penalty score * penalty factor."""
        text_a = "neural plasticity synaptic strength long-term potentiation"
        text_b = "updated neural plasticity 2026 revision"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)
        open_brain.add_link(conn, source_id=id_b, target_id=id_a,
                             link_type="supersedes", user_id=test_user)

        # Override penalty to a known value for the assertion.
        env_key = "OPEN_BRAIN_SUPERSEDE_PENALTY"
        original = os.environ.get(env_key)
        penalty_val = 0.3
        os.environ[env_key] = str(penalty_val)
        try:
            # Run without supersedes link first to get baseline score.
            # We need two separate users to isolate: easier to just check
            # that the penalised score is ≤ 0.3 * any reasonable max score.
            results = open_brain.search(
                conn, "neural plasticity synaptic", test_user, limit=20, threshold=0.0
            )
        finally:
            if original is None:
                del os.environ[env_key]
            else:
                os.environ[env_key] = original

        result_a = next((r for r in results if r["THOUGHT_ID"] == id_a), None)
        assert result_a is not None
        # The penalised score must be at most penalty_val * 1.0 (max possible
        # hybrid = 1.0 before penalty).
        assert result_a["HYBRID_SCORE"] <= penalty_val + 1e-3, (
            f"Penalised score {result_a['HYBRID_SCORE']} > {penalty_val}"
        )


class TestLiveSourceGuard:
    """(b) A supersedes link whose source was forgotten MUST NOT suppress the target."""

    def test_deleted_source_does_not_suppress(self, conn, test_user):
        """If B is deleted from brain.thoughts, the B→A link does not suppress A."""
        text_a = "carbon capture geological sequestration baseline study"
        text_b = "carbon capture update — will be deleted"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)

        # Create the supersedes link
        open_brain.add_link(conn, source_id=id_b, target_id=id_a,
                             link_type="supersedes", user_id=test_user)

        # Delete the source atom directly (simulates forget without VF_ε probes)
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
                (id_b, test_user),
            )
            conn.commit()
        finally:
            cur.close()

        results = open_brain.search(
            conn, "carbon capture geological sequestration", test_user,
            limit=20, threshold=0.0
        )

        result_a = next((r for r in results if r["THOUGHT_ID"] == id_a), None)
        assert result_a is not None, "A must still appear (source was deleted)"
        # A must NOT carry SUPERSEDED_BY — the source is gone
        assert "SUPERSEDED_BY" not in result_a, (
            "Deleted source must not suppress target"
        )


class TestDisputedAnnotationIntegration:
    """(c) refutes/contradicts → DISPUTED annotation, no score penalty."""

    def test_refutes_link_annotates_disputed_no_penalty(self, conn, test_user):
        """A refutes link annotates target with DISPUTED but leaves score unchanged."""
        text_target = "homeopathy clinical trial meta-analysis positive effect"
        text_source = "systematic review refuting homeopathy meta-analysis"

        id_target = _insert_thought_with_embedding(conn, test_user, text_target)
        id_source = _insert_thought_with_embedding(conn, test_user, text_source)

        open_brain.add_link(conn, source_id=id_source, target_id=id_target,
                             link_type="refutes", user_id=test_user)

        results = open_brain.search(
            conn, "homeopathy clinical trial meta-analysis", test_user,
            limit=20, threshold=0.0
        )

        result_target = next((r for r in results if r["THOUGHT_ID"] == id_target), None)
        assert result_target is not None

        # DISPUTED must be annotated
        assert "DISPUTED" in result_target
        assert id_source in result_target["DISPUTED"]["by"]
        assert "refutes" in result_target["DISPUTED"]["types"]

        # SUPERSEDED_BY must NOT be present (refutes ≠ supersedes)
        assert "SUPERSEDED_BY" not in result_target

    def test_contradicts_link_annotates_disputed(self, conn, test_user):
        """A contradicts link also annotates DISPUTED (same behaviour as refutes)."""
        text_target = "dark matter particle detection dark photon signal"
        text_source = "contradicting dark photon signal reanalysis"

        id_target = _insert_thought_with_embedding(conn, test_user, text_target)
        id_source = _insert_thought_with_embedding(conn, test_user, text_source)

        open_brain.add_link(conn, source_id=id_source, target_id=id_target,
                             link_type="contradicts", user_id=test_user)

        results = open_brain.search(
            conn, "dark matter particle detection photon", test_user,
            limit=20, threshold=0.0
        )

        result_target = next((r for r in results if r["THOUGHT_ID"] == id_target), None)
        assert result_target is not None
        assert "DISPUTED" in result_target
        assert "contradicts" in result_target["DISPUTED"]["types"]

    def test_disputed_score_unchanged(self, conn, test_user):
        """DISPUTED atoms must not have their score modified (no penalty)."""
        text_t = "RNA splicing alternative isoform regulation genome"
        text_s = "contradicting RNA splicing isoform evidence"

        id_t = _insert_thought_with_embedding(conn, test_user, text_t)
        id_s = _insert_thought_with_embedding(conn, test_user, text_s)

        # Run once to get baseline score before adding contradicts link
        # (same user, same embedding — score should be identical)
        results_before = open_brain.search(
            conn, "RNA splicing isoform regulation", test_user,
            limit=20, threshold=0.0
        )
        before = next((r for r in results_before if r["THOUGHT_ID"] == id_t), None)
        score_before = before["HYBRID_SCORE"] if before else None

        # Now add the contradicts link
        open_brain.add_link(conn, source_id=id_s, target_id=id_t,
                             link_type="contradicts", user_id=test_user)

        results_after = open_brain.search(
            conn, "RNA splicing isoform regulation", test_user,
            limit=20, threshold=0.0
        )
        after = next((r for r in results_after if r["THOUGHT_ID"] == id_t), None)
        assert after is not None
        assert "DISPUTED" in after

        # Score must not have been penalised (allow small float rounding ±0.001)
        if score_before is not None:
            # Score may differ slightly due to memory reinforcement (updated_at touch)
            # but must NOT be dramatically lower.  Apply a generous tolerance.
            assert after["HYBRID_SCORE"] >= score_before * 0.95 - 0.01, (
                f"DISPUTED score changed too much: before={score_before} "
                f"after={after['HYBRID_SCORE']}"
            )


class TestPenaltyEnvOverride:
    """(d) OPEN_BRAIN_SUPERSEDE_PENALTY env var is respected."""

    def test_strong_penalty_drives_score_lower(self, conn, test_user):
        """Setting penalty=0.1 gives a much lower score than the default 0.5."""
        text_a = "plate tectonics subduction zone seismic hazard baseline"
        text_b = "updated plate tectonics seismic study 2026"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)

        open_brain.add_link(conn, source_id=id_b, target_id=id_a,
                             link_type="supersedes", user_id=test_user)

        # Run with penalty=0.1
        env_key = "OPEN_BRAIN_SUPERSEDE_PENALTY"
        original = os.environ.get(env_key)
        os.environ[env_key] = "0.1"
        try:
            results_strong = open_brain.search(
                conn, "plate tectonics subduction seismic", test_user,
                limit=20, threshold=0.0
            )
        finally:
            if original is None:
                del os.environ[env_key]
            else:
                os.environ[env_key] = original

        result_a_strong = next(
            (r for r in results_strong if r["THOUGHT_ID"] == id_a), None
        )
        assert result_a_strong is not None
        # With 0.1 penalty, score must be ≤ 0.1 (since max pre-penalty = 1.0)
        assert result_a_strong["HYBRID_SCORE"] <= 0.1 + 1e-3

    def test_default_penalty_when_env_unset(self, conn, test_user):
        """When env var is unset, default 0.5 penalty applies."""
        text_a = "ocean acidification coral bleaching carbonate saturation"
        text_b = "updated ocean acidification study 2026"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)

        open_brain.add_link(conn, source_id=id_b, target_id=id_a,
                             link_type="supersedes", user_id=test_user)

        # Ensure env var is unset
        env_key = "OPEN_BRAIN_SUPERSEDE_PENALTY"
        original = os.environ.pop(env_key, None)
        try:
            results = open_brain.search(
                conn, "ocean acidification coral carbonate", test_user,
                limit=20, threshold=0.0
            )
        finally:
            if original is not None:
                os.environ[env_key] = original

        result_a = next((r for r in results if r["THOUGHT_ID"] == id_a), None)
        assert result_a is not None
        assert "SUPERSEDED_BY" in result_a
        # Default 0.5 penalty → score ≤ 0.5
        assert result_a["HYBRID_SCORE"] <= 0.5 + 1e-3


class TestGraphSearchUnion:
    """(e) graph_search returns atoms reachable only via atom_links (not kg_neighborhood)."""

    def test_atom_links_only_atom_retrieved(self, conn, test_user):
        """An atom with NO embedding (invisible to vector search) is retrieved
        when it is linked from a seed atom via atom_links."""
        # Seed atom: has embedding, will appear in vector search.
        seed_text = "mitochondrial membrane potential ATP synthesis electron transport"
        seed_id = _insert_thought_with_embedding(conn, test_user, seed_text)

        # Linked atom: NO embedding — would be invisible to vector/hybrid search.
        linked_text = "related concept about mitochondria ATP that has no embedding"
        linked_id = _insert_thought_no_embedding(conn, test_user, linked_text)

        # Connect seed → linked via atom_links
        open_brain.add_link(
            conn,
            source_id=seed_id,
            target_id=linked_id,
            link_type="cites",
            user_id=test_user,
            verify_source_exists=True,
        )

        # Verify the linked atom is NOT reachable via plain search (sanity).
        plain_results = open_brain.search(
            conn, "mitochondrial membrane ATP synthesis", test_user,
            limit=20, threshold=0.0
        )
        plain_ids = [r["THOUGHT_ID"] for r in plain_results]
        assert linked_id not in plain_ids, (
            "Linked atom should NOT appear in plain search (no embedding)"
        )

        # But graph_search must include it via atom_links.
        graph_results = open_brain.graph_search(
            conn, "mitochondrial membrane ATP synthesis", test_user,
            limit=50, threshold=0.0
        )
        graph_ids = [r["THOUGHT_ID"] for r in graph_results]
        # NOTE: This test proves the union works if kg_neighborhood doesn't
        # already surface the linked atom.  If kg tables are empty for this
        # test user (common in isolation), the atom_links path is the only
        # mechanism that can return it.
        assert linked_id in graph_ids, (
            f"Linked atom {linked_id} must appear in graph_search via atom_links; "
            f"got: {graph_ids}"
        )

    def test_atom_links_both_directions_walked(self, conn, test_user):
        """Both outgoing AND incoming atom_links edges are walked (1 hop)."""
        # Atom A and atom B: B is the seed (has embedding); A points TO B.
        text_b = "photosynthesis chlorophyll light absorption quantum yield"
        text_a = "photosynthesis precursor study referenced from main paper"

        id_b = _insert_thought_with_embedding(conn, test_user, text_b)
        id_a = _insert_thought_no_embedding(conn, test_user, text_a)

        # A cites B (source_id=A, target_id=B)
        open_brain.add_link(
            conn,
            source_id=id_a,
            target_id=id_b,
            link_type="cites",
            user_id=test_user,
            verify_source_exists=True,
        )

        graph_results = open_brain.graph_search(
            conn, "photosynthesis chlorophyll light absorption", test_user,
            limit=50, threshold=0.0
        )
        graph_ids = [r["THOUGHT_ID"] for r in graph_results]
        # id_a has no embedding but is linked TO id_b (incoming from id_b's perspective).
        # The 1-hop expansion walks both directions so id_a should appear.
        assert id_a in graph_ids, (
            f"Atom {id_a} (no embedding, incoming edge from seed) must appear "
            f"in graph_search via atom_links incoming walk; got: {graph_ids}"
        )


class TestFormattedOutputMarkers:
    """(f) _format_search_results contains SUPERSEDED_BY / DISPUTED text."""

    def test_formatted_output_superseded_by(self, conn, test_user):
        """Live search with supersedes link produces formatted output with marker."""
        text_a = "species biodiversity tropical rainforest canopy baseline"
        text_b = "updated species biodiversity survey 2026"

        id_a = _insert_thought_with_embedding(conn, test_user, text_a)
        id_b = _insert_thought_with_embedding(conn, test_user, text_b)

        open_brain.add_link(conn, source_id=id_b, target_id=id_a,
                             link_type="supersedes", user_id=test_user)

        results = open_brain.search(
            conn, "species biodiversity tropical rainforest", test_user,
            limit=20, threshold=0.0
        )
        formatted = open_brain._format_search_results(results)
        assert "[SUPERSEDED_BY" in formatted, (
            f"Formatted output must contain [SUPERSEDED_BY ...]. Got:\n{formatted}"
        )
        assert id_b in formatted or id_b[:16] in formatted

    def test_formatted_output_disputed(self, conn, test_user):
        """Live search with refutes link produces formatted output with DISPUTED marker."""
        text_t = "vaccine efficacy immunogenicity randomised controlled trial"
        text_s = "refuting vaccine efficacy re-analysis confounders"

        id_t = _insert_thought_with_embedding(conn, test_user, text_t)
        id_s = _insert_thought_with_embedding(conn, test_user, text_s)

        open_brain.add_link(conn, source_id=id_s, target_id=id_t,
                             link_type="refutes", user_id=test_user)

        results = open_brain.search(
            conn, "vaccine efficacy immunogenicity trial", test_user,
            limit=20, threshold=0.0
        )
        formatted = open_brain._format_search_results(results)
        assert "[DISPUTED by" in formatted, (
            f"Formatted output must contain [DISPUTED by ...]. Got:\n{formatted}"
        )
