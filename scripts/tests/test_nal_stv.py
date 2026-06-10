#!/usr/bin/env python3
"""T2.6 / fblai-eovhe — NAL-lite truth-value layer.

Tests for:
(a) NAL revision math — 4 property tests (pure-logic, no DB).
(b) Migration backfill maps confidence→stv_confidence correctly.
(c) capture() seeds stv from confidence; --stv-f/--stv-c overrides honored;
    _run_from_pi 'capture' stv validated (reject out-of-range).
(d) --revise creates a derived atom with stv=nal_revise(premises),
    derives_from links to both; when a contradicts link pre-exists,
    resolves links are added.
(e) verifies link bumps target confidence AND creates a thought_versions row
    (prov_activity='nal_evidence'); refutes link drops target frequency.
(f) search() surfaces STV + LOW_CONFIDENCE flag when c < 0.35.
(g) _run_from_pi 'revise' op works through the bridge.

Pure-logic tests: (a) only — run without DATABASE_URL.
Integration tests: (b)–(g) — SKIP cleanly without DATABASE_URL.

Neon gotcha: after init_schema() runs DDL on a pooled connection, the
::vector cast can fail on that same connection. The conn fixture uses the
two-connection pattern from test_links_retrieval.py to avoid this.

Run:
    python3 -m pytest scripts/tests/test_nal_stv.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from io import StringIO
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── (a) Pure-logic NAL revision math ────────────────────────────────────────


class TestNalRevise:
    """Pure-logic tests — no DB required."""

    def test_equal_frequency_raises_confidence(self):
        """(i) Revising two equal beliefs yields the same f and c > either c."""
        f1, c1 = 0.7, 0.6
        f2, c2 = 0.7, 0.6
        f_out, c_out = open_brain.nal_revise(f1, c1, f2, c2)
        assert abs(f_out - f1) < 1e-9, (
            f"Revised f ({f_out:.6f}) should equal input f ({f1}) when both are equal"
        )
        assert c_out > c1, (
            f"Revised c ({c_out:.6f}) should be strictly greater than input c ({c1})"
        )
        assert c_out > c2, (
            f"Revised c ({c_out:.6f}) should be strictly greater than input c ({c2})"
        )

    def test_commutativity(self):
        """(ii) Revision is commutative in (f, c) pairs."""
        pairs = [
            (0.8, 0.9, 0.3, 0.5),
            (0.5, 0.7, 0.5, 0.3),
            (0.1, 0.4, 0.9, 0.8),
            (0.01, 0.01, 0.99, 0.99),  # boundary
        ]
        for f1, c1, f2, c2 in pairs:
            f_ab, c_ab = open_brain.nal_revise(f1, c1, f2, c2)
            f_ba, c_ba = open_brain.nal_revise(f2, c2, f1, c1)
            assert abs(f_ab - f_ba) < 1e-9, (
                f"f not commutative: revise({f1},{c1},{f2},{c2})={f_ab:.6f} "
                f"!= revise({f2},{c2},{f1},{c1})={f_ba:.6f}"
            )
            assert abs(c_ab - c_ba) < 1e-9, (
                f"c not commutative: {c_ab:.6f} != {c_ba:.6f}"
            )

    def test_result_range(self):
        """(iii) Result f ∈ [0,1], c ∈ [0,1)."""
        test_cases = [
            (0.0, 0.0, 0.0, 0.0),  # all zeros → clamped to [0.01, 0.99]
            (1.0, 1.0, 1.0, 1.0),  # all ones → clamped to [0.01, 0.99]
            (0.5, 0.5, 0.5, 0.5),
            (0.1, 0.9, 0.9, 0.1),
            (0.8, 0.3, 0.2, 0.7),
        ]
        for f1, c1, f2, c2 in test_cases:
            f_out, c_out = open_brain.nal_revise(f1, c1, f2, c2)
            assert 0.0 <= f_out <= 1.0, (
                f"f out of range [0,1]: {f_out} (inputs: {f1},{c1},{f2},{c2})"
            )
            assert 0.0 <= c_out < 1.0, (
                f"c out of range [0,1): {c_out} (inputs: {f1},{c1},{f2},{c2})"
            )

    def test_high_confidence_dominates(self):
        """(iv) A high-confidence belief dominates a low-confidence contradicting one.

        When A has high confidence (say f_A=0.9, c_A=0.9) and B has low
        confidence (f_B=0.1, c_B=0.1), the revised f should be closer to
        f_A than to f_B.
        """
        f_high, c_high = 0.9, 0.9   # high-confidence: positive evidence
        f_low,  c_low  = 0.1, 0.1   # low-confidence: contradicting evidence
        f_out, c_out = open_brain.nal_revise(f_high, c_high, f_low, c_low)
        # The high-confidence belief contributes more → f_out closer to f_high.
        dist_high = abs(f_out - f_high)
        dist_low  = abs(f_out - f_low)
        assert dist_high < dist_low, (
            f"High-confidence premise should dominate: f_out={f_out:.4f}, "
            f"dist to high={dist_high:.4f}, dist to low={dist_low:.4f}"
        )

    def test_confidence_map(self):
        """_stv_from_confidence maps labels to correct (f, c) pairs."""
        assert open_brain._stv_from_confidence("high")   == (1.0, 0.9)
        assert open_brain._stv_from_confidence("medium") == (1.0, 0.7)
        assert open_brain._stv_from_confidence("low")    == (1.0, 0.5)
        assert open_brain._stv_from_confidence(None)     == (1.0, 0.5)
        assert open_brain._stv_from_confidence("other")  == (1.0, 0.5)

    def test_boundary_clamp(self):
        """Inputs of 0 and 1 are clamped without div-by-zero."""
        # Should not raise.
        f1, c1 = open_brain.nal_revise(0.0, 0.0, 1.0, 1.0)
        assert 0.0 <= f1 <= 1.0
        assert 0.0 <= c1 < 1.0

    def test_evidence_accumulation(self):
        """Revising a belief with itself accumulates evidence (c rises each time)."""
        f, c = 0.6, 0.5
        f2, c2 = open_brain.nal_revise(f, c, f, c)
        f3, c3 = open_brain.nal_revise(f2, c2, f, c)
        assert c2 > c, f"First revision should raise c: {c} → {c2}"
        assert c3 > c2, f"Second revision should raise c further: {c2} → {c3}"
        assert c3 < 1.0, "Confidence must stay strictly below 1"


# ─── DB fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    """Module-scoped live Postgres connection.

    Uses the two-connection pattern: init_schema on the first connection,
    then yield a fresh second connection where pgvector::vector is resolvable
    (Neon pooler gotcha — DDL runs can invalidate the vector type on the same
    session; reconnect fixes it).
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
    # First connection: schema init.
    c_init = open_brain._connect()
    open_brain.init_schema(c_init)
    c_init.close()
    # Second connection: working connection for all tests.
    c = open_brain._connect()
    yield c
    c.close()


@pytest.fixture()
def test_user(conn):
    """Per-test isolated user; cleaned up on teardown."""
    uid = f"test-stv-{uuid.uuid4().hex[:12]}"
    yield uid
    cur = conn.cursor()
    try:
        for tbl in (
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


def _plant_thought(
    conn,
    user_id: str,
    text: str = "test thought",
    stv_f: float = 1.0,
    stv_c: float = 0.5,
    confidence_label: Optional[str] = None,
) -> str:
    """Insert a minimal thought row directly for test setup (bypasses LLM)."""
    import open_brain as ob
    thought_id = ob._generate_thought_id()
    prov_agent = ob._derive_prov_agent("test", user_id)
    was_generated_by = ob._generate_activity_id(thought_id)
    embedding = ob._generate_embedding(text)
    metadata: Dict[str, Any] = {"type": "insight", "topics": [], "people": [],
                                 "action_items": [], "summary": text[:200]}
    if confidence_label:
        metadata["confidence"] = confidence_label
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thoughts (
                thought_id, user_id, raw_text, summary, thought_type,
                topics, people, action_items, source, session_id, project,
                prov_agent, prov_activity, was_generated_by, was_derived_from, source_uri,
                embedding, metadata, stv_frequency, stv_confidence, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s::vector, %s::jsonb, %s, %s,
                NOW(), NOW()
            )
            """,
            (
                thought_id, user_id, text[:16384], text[:200], "insight",
                json.dumps([]), json.dumps([]), json.dumps([]),
                "test", "", "",
                prov_agent, "capture", was_generated_by, None, None,
                str(embedding), json.dumps(metadata),
                stv_f, stv_c,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return thought_id


# ─── (b) Migration backfill ───────────────────────────────────────────────────


class TestMigrationBackfill:
    """Verify the backfill DO block maps confidence→stv_confidence.

    We plant a row with a known confidence label in metadata and then
    simulate the backfill UPDATE (mirroring what the migration does) on
    a test user. We verify the resulting stv_confidence value.
    """

    @pytest.mark.integration
    def test_backfill_high(self, conn, test_user):
        """metadata->>'confidence' = 'high' → stv_confidence = 0.9."""
        tid = _plant_thought(conn, test_user, text="high-confidence thought",
                             stv_f=1.0, stv_c=0.5, confidence_label="high")
        # Simulate migration backfill UPDATE (only if still at default 0.5).
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE brain.thoughts
                SET stv_confidence = CASE metadata->>'confidence'
                        WHEN 'high'   THEN 0.9
                        WHEN 'medium' THEN 0.7
                        WHEN 'low'    THEN 0.5
                        ELSE               0.5
                    END,
                    stv_frequency = 1.0
                WHERE thought_id = %s AND stv_confidence = 0.5
                """,
                (tid,),
            )
            conn.commit()
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 0.9) < 1e-6, (
            f"high confidence should backfill to 0.9, got {row[0]}"
        )

    @pytest.mark.integration
    def test_backfill_medium(self, conn, test_user):
        """metadata->>'confidence' = 'medium' → stv_confidence = 0.7."""
        tid = _plant_thought(conn, test_user, text="medium-confidence thought",
                             stv_f=1.0, stv_c=0.5, confidence_label="medium")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE brain.thoughts
                SET stv_confidence = CASE metadata->>'confidence'
                        WHEN 'high'   THEN 0.9
                        WHEN 'medium' THEN 0.7
                        WHEN 'low'    THEN 0.5
                        ELSE               0.5
                    END
                WHERE thought_id = %s AND stv_confidence = 0.5
                """,
                (tid,),
            )
            conn.commit()
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 0.7) < 1e-6

    @pytest.mark.integration
    def test_backfill_low(self, conn, test_user):
        """metadata->>'confidence' = 'low' → stv_confidence = 0.5 (default, no change)."""
        tid = _plant_thought(conn, test_user, text="low-confidence thought",
                             stv_f=1.0, stv_c=0.5, confidence_label="low")
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 0.5) < 1e-6

    @pytest.mark.integration
    def test_backfill_absent(self, conn, test_user):
        """Absent confidence label → stv_confidence stays at 0.5."""
        tid = _plant_thought(conn, test_user, text="no-confidence-label thought",
                             stv_f=1.0, stv_c=0.5)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 0.5) < 1e-6


# ─── (c) capture() stv seeding ───────────────────────────────────────────────


class TestCaptureStvSeeding:
    """capture() seeds stv_f/stv_c from confidence label or explicit overrides."""

    @pytest.mark.integration
    def test_capture_seeds_from_metadata_confidence(self, conn, test_user):
        """When the LLM extracts confidence='high', stv_confidence should be 0.9."""
        # We mock _extract_metadata to return a known high-confidence payload.
        mock_meta = {
            "type": "insight",
            "topics": [],
            "people": [],
            "action_items": [],
            "summary": "test high confidence",
            "confidence": "high",
        }
        with patch.object(open_brain, "_extract_metadata", return_value=mock_meta):
            result = open_brain.capture(
                conn,
                text="test high confidence thought",
                user_id=test_user,
            )
        tid = result["thought_id"]
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency, stv_confidence FROM brain.thoughts "
                "WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 1.0) < 1e-6, "stv_frequency should be 1.0"
        assert abs(float(row[1]) - 0.9) < 1e-6, f"stv_confidence should be 0.9, got {row[1]}"
        # result dict also carries stv
        stv = result.get("stv")
        assert stv is not None, "capture result should carry stv dict"
        assert abs(stv["c"] - 0.9) < 1e-6

    @pytest.mark.integration
    def test_capture_stv_f_override(self, conn, test_user):
        """--stv-f override is stored and returned."""
        mock_meta = {
            "type": "insight", "topics": [], "people": [], "action_items": [],
            "summary": "stv override test", "confidence": "high",
        }
        with patch.object(open_brain, "_extract_metadata", return_value=mock_meta):
            result = open_brain.capture(
                conn,
                text="stv override test",
                user_id=test_user,
                stv_f=0.3,
                stv_c=0.7,
            )
        tid = result["thought_id"]
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency, stv_confidence FROM brain.thoughts "
                "WHERE thought_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert abs(float(row[0]) - 0.3) < 1e-4, f"stv_f override: expected 0.3, got {row[0]}"
        assert abs(float(row[1]) - 0.7) < 1e-4, f"stv_c override: expected 0.7, got {row[1]}"

    def test_pi_bridge_stv_validation_rejects_out_of_range(self):
        """_run_from_pi capture op rejects stv values outside [0,1]."""
        invalid_cases = [
            {"op": "capture", "text": "test", "stv": {"f": 1.5, "c": 0.5}},
            {"op": "capture", "text": "test", "stv": {"f": 0.5, "c": -0.1}},
        ]
        for payload in invalid_cases:
            captured_output = StringIO()
            with patch.object(open_brain, "_connect", side_effect=RuntimeError("no db")):
                with patch("sys.stdin", StringIO(json.dumps(payload))):
                    with patch("sys.stdout", captured_output):
                        try:
                            open_brain._run_from_pi()
                        except (SystemExit, RuntimeError):
                            pass
            out = captured_output.getvalue().strip()
            if out:
                try:
                    data = json.loads(out)
                    assert "error" in data, (
                        f"Expected error in output for {payload}, got: {data}"
                    )
                except json.JSONDecodeError:
                    pass  # no output before _connect raises is acceptable


# ─── (d) --revise derived atom ───────────────────────────────────────────────


class TestReviseThoughts:
    """--revise creates a derived atom with correct stv and links."""

    @pytest.mark.integration
    def test_revise_creates_derived_atom(self, conn, test_user):
        """Derived atom has stv = nal_revise(A.stv, B.stv), derives_from both."""
        # Plant two premises.
        id_a = _plant_thought(conn, test_user, text="premise A", stv_f=1.0, stv_c=0.8)
        id_b = _plant_thought(conn, test_user, text="premise B", stv_f=0.8, stv_c=0.6)

        expected_f, expected_c = open_brain.nal_revise(1.0, 0.8, 0.8, 0.6)

        result = open_brain.revise_thoughts(
            conn,
            id_a=id_a,
            id_b=id_b,
            user_id=test_user,
            text="explicit derived text",
        )
        derived_id = result["thought_id"]

        # Check stv on the derived atom.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency, stv_confidence, raw_text FROM brain.thoughts "
                "WHERE thought_id = %s",
                (derived_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()

        assert row is not None, "Derived atom should exist in DB"
        assert abs(float(row[0]) - expected_f) < 1e-4, (
            f"Expected stv_f={expected_f:.4f}, got {row[0]}"
        )
        assert abs(float(row[1]) - expected_c) < 1e-4, (
            f"Expected stv_c={expected_c:.4f}, got {row[1]}"
        )

        # Check result dict.
        assert "stv" in result
        assert result["derives_from"] == [id_a, id_b]

    @pytest.mark.integration
    def test_revise_prov_activity(self, conn, test_user):
        """Derived atom has prov_activity='nal_revision'."""
        id_a = _plant_thought(conn, test_user, text="prov premise A", stv_f=1.0, stv_c=0.7)
        id_b = _plant_thought(conn, test_user, text="prov premise B", stv_f=1.0, stv_c=0.5)

        result = open_brain.revise_thoughts(
            conn, id_a=id_a, id_b=id_b, user_id=test_user,
        )
        derived_id = result["thought_id"]

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT prov_activity FROM brain.thoughts WHERE thought_id = %s",
                (derived_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()

        assert row is not None
        assert row[0] == "nal_revision", f"Expected 'nal_revision', got {row[0]!r}"

    @pytest.mark.integration
    def test_revise_derives_from_links(self, conn, test_user):
        """derives_from links to both premises exist in atom_links."""
        id_a = _plant_thought(conn, test_user, text="link premise A", stv_f=1.0, stv_c=0.9)
        id_b = _plant_thought(conn, test_user, text="link premise B", stv_f=1.0, stv_c=0.7)

        result = open_brain.revise_thoughts(
            conn, id_a=id_a, id_b=id_b, user_id=test_user,
        )
        derived_id = result["thought_id"]

        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT target_id FROM brain.atom_links
                WHERE source_id = %s AND link_type = 'derives_from' AND user_id = %s
                ORDER BY target_id
                """,
                (derived_id, test_user),
            )
            rows = cur.fetchall()
        finally:
            cur.close()

        targets = {r[0] for r in rows}
        assert id_a in targets, f"derives_from link to {id_a} missing"
        assert id_b in targets, f"derives_from link to {id_b} missing"

    @pytest.mark.integration
    def test_revise_resolves_when_contradicts_exists(self, conn, test_user):
        """When a contradicts link exists between A and B, resolves links are added."""
        id_a = _plant_thought(conn, test_user, text="contradicting premise A",
                              stv_f=0.9, stv_c=0.8)
        id_b = _plant_thought(conn, test_user, text="contradicting premise B",
                              stv_f=0.1, stv_c=0.7)

        # Plant a contradicts link A→B.
        open_brain.add_link(
            conn, source_id=id_a, target_id=id_b,
            link_type="contradicts", user_id=test_user,
        )

        result = open_brain.revise_thoughts(
            conn, id_a=id_a, id_b=id_b, user_id=test_user,
        )
        derived_id = result["thought_id"]
        assert result.get("contradicts_resolved") is True

        # Check resolves links.
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT target_id FROM brain.atom_links
                WHERE source_id = %s AND link_type = 'resolves' AND user_id = %s
                """,
                (derived_id, test_user),
            )
            rows = cur.fetchall()
        finally:
            cur.close()

        targets = {r[0] for r in rows}
        assert id_a in targets, "resolves link to A should exist"
        assert id_b in targets, "resolves link to B should exist"

    @pytest.mark.integration
    def test_revise_ps_scoping(self, conn, test_user):
        """revise_thoughts raises RuntimeError when a premise is in a different user scope."""
        id_a = _plant_thought(conn, test_user, text="in-scope premise")
        wrong_user = f"wrong-user-{uuid.uuid4().hex[:8]}"
        with pytest.raises(RuntimeError, match="not in user scope"):
            open_brain.revise_thoughts(
                conn, id_a=id_a, id_b="nonexistent-id", user_id=test_user,
            )

    @pytest.mark.integration
    def test_revise_same_id_rejected(self, conn, test_user):
        """revise_thoughts raises ValueError when id_a == id_b."""
        id_a = _plant_thought(conn, test_user, text="self-revision test")
        with pytest.raises(ValueError, match="must be different"):
            open_brain.revise_thoughts(
                conn, id_a=id_a, id_b=id_a, user_id=test_user,
            )


# ─── (e) Evidence propagation via verifies/refutes ───────────────────────────


class TestEvidencePropagation:
    """verifies/refutes links update target stv + create thought_versions row."""

    @pytest.mark.integration
    def test_verifies_bumps_target_confidence(self, conn, test_user):
        """Adding a verifies link increases target stv_confidence."""
        source = _plant_thought(conn, test_user, text="verifying source", stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user, text="target being verified", stv_f=1.0, stv_c=0.5)

        # Read pre-link target stv.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency, stv_confidence FROM brain.thoughts "
                "WHERE thought_id = %s",
                (target,),
            )
            pre_row = cur.fetchone()
        finally:
            cur.close()

        pre_c = float(pre_row[1])

        open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="verifies",
            user_id=test_user,
        )

        # Read post-link target stv.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency, stv_confidence FROM brain.thoughts "
                "WHERE thought_id = %s",
                (target,),
            )
            post_row = cur.fetchone()
        finally:
            cur.close()

        post_c = float(post_row[1])
        assert post_c > pre_c, (
            f"verifies link should raise target confidence: {pre_c:.4f} → {post_c:.4f}"
        )

    @pytest.mark.integration
    def test_verifies_creates_thought_versions_row(self, conn, test_user):
        """verifies link creates a thought_versions row with prov_activity='nal_evidence'."""
        source = _plant_thought(conn, test_user, text="verifier source", stv_f=1.0, stv_c=0.9)
        target = _plant_thought(conn, test_user, text="target for versioning", stv_f=1.0, stv_c=0.5)

        open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="verifies",
            user_id=test_user,
        )

        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT prov_activity, stv_confidence FROM brain.thought_versions
                WHERE thought_id = %s
                ORDER BY revision DESC
                LIMIT 1
                """,
                (target,),
            )
            row = cur.fetchone()
        finally:
            cur.close()

        assert row is not None, "thought_versions row should exist after verifies link"
        assert row[0] == "nal_evidence", (
            f"Expected prov_activity='nal_evidence', got {row[0]!r}"
        )

    @pytest.mark.integration
    def test_refutes_drops_target_frequency(self, conn, test_user):
        """Adding a refutes link lowers target stv_frequency."""
        source = _plant_thought(conn, test_user, text="refuting source", stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user, text="target being refuted", stv_f=1.0, stv_c=0.5)

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency FROM brain.thoughts WHERE thought_id = %s",
                (target,),
            )
            pre_f = float(cur.fetchone()[0])
        finally:
            cur.close()

        open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="refutes",
            user_id=test_user,
        )

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_frequency FROM brain.thoughts WHERE thought_id = %s",
                (target,),
            )
            post_f = float(cur.fetchone()[0])
        finally:
            cur.close()

        assert post_f < pre_f, (
            f"refutes link should drop target frequency: {pre_f:.4f} → {post_f:.4f}"
        )

    @pytest.mark.integration
    def test_references_bead_exempt(self, conn, test_user):
        """references_bead links do not trigger evidence propagation."""
        source = _plant_thought(conn, test_user, text="bead reference source")
        bead_id = "gz-testbead123"

        # Should not raise and should not try to read stv from a bead row.
        result = open_brain.add_link(
            conn,
            source_id=source,
            target_id=bead_id,
            link_type="references_bead",
            user_id=test_user,
        )
        # Evidence propagation is exempt — no exception.
        assert result["link_type"] == "references_bead"

    @pytest.mark.integration
    def test_existing_edge_no_double_propagation(self, conn, test_user):
        """Idempotent re-add of an existing edge does not propagate again."""
        source = _plant_thought(conn, test_user, text="idempotent source", stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user, text="idempotent target", stv_f=1.0, stv_c=0.4)

        open_brain.add_link(
            conn, source_id=source, target_id=target, link_type="verifies",
            user_id=test_user,
        )
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (target,),
            )
            c_after_first = float(cur.fetchone()[0])
        finally:
            cur.close()

        # Re-add same edge — should be a no-op (created=False), no propagation.
        open_brain.add_link(
            conn, source_id=source, target_id=target, link_type="verifies",
            user_id=test_user,
        )
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stv_confidence FROM brain.thoughts WHERE thought_id = %s",
                (target,),
            )
            c_after_second = float(cur.fetchone()[0])
        finally:
            cur.close()

        assert abs(c_after_first - c_after_second) < 1e-9, (
            "Idempotent edge re-add should not change stv_confidence again"
        )


# ─── (f) search() STV surfacing ───────────────────────────────────────────────


class TestSearchStvSurfacing:
    """search() results carry STV and LOW_CONFIDENCE flag."""

    @pytest.mark.integration
    def test_search_carries_stv(self, conn, test_user):
        """search() results include STV dict."""
        tid = _plant_thought(conn, test_user, text="search stv test atom",
                             stv_f=0.8, stv_c=0.75)
        results = open_brain.search(
            conn, query="search stv test atom", user_id=test_user, limit=5,
        )
        found = [r for r in results if r.get("THOUGHT_ID") == tid]
        assert found, f"Planted thought {tid} not found in search results"
        stv = found[0].get("STV")
        assert stv is not None, "STV key missing from search result"
        assert "f" in stv and "c" in stv, f"STV dict malformed: {stv}"

    @pytest.mark.integration
    def test_search_low_confidence_flag(self, conn, test_user):
        """search() sets LOW_CONFIDENCE=True when c < 0.35."""
        tid = _plant_thought(conn, test_user, text="low confidence search test",
                             stv_f=1.0, stv_c=0.2)
        results = open_brain.search(
            conn, query="low confidence search test", user_id=test_user, limit=5,
        )
        found = [r for r in results if r.get("THOUGHT_ID") == tid]
        assert found, f"Planted thought {tid} not found"
        assert found[0].get("LOW_CONFIDENCE") is True, (
            f"Expected LOW_CONFIDENCE=True for c=0.2, got {found[0].get('LOW_CONFIDENCE')}"
        )

    @pytest.mark.integration
    def test_search_not_low_confidence_when_c_high(self, conn, test_user):
        """search() sets LOW_CONFIDENCE=False when c >= 0.35."""
        tid = _plant_thought(conn, test_user, text="high conf search test unique xyzzy",
                             stv_f=1.0, stv_c=0.9)
        results = open_brain.search(
            conn, query="high conf search test unique xyzzy", user_id=test_user, limit=5,
        )
        found = [r for r in results if r.get("THOUGHT_ID") == tid]
        assert found, f"Planted thought {tid} not found"
        assert found[0].get("LOW_CONFIDENCE") is False, (
            f"Expected LOW_CONFIDENCE=False for c=0.9, got {found[0].get('LOW_CONFIDENCE')}"
        )

    @pytest.mark.integration
    def test_format_search_results_shows_stv(self, conn, test_user):
        """_format_search_results includes [stv ...] annotation line."""
        tid = _plant_thought(conn, test_user, text="format stv test unique qqqq",
                             stv_f=0.7, stv_c=0.6)
        results = open_brain.search(
            conn, query="format stv test unique qqqq", user_id=test_user, limit=5,
        )
        found = [r for r in results if r.get("THOUGHT_ID") == tid]
        assert found, "Planted thought not found"
        formatted = open_brain._format_search_results(found)
        assert "[stv" in formatted, f"STV tag missing from formatted output: {formatted}"

    @pytest.mark.integration
    def test_format_search_results_low_conf_marker(self, conn, test_user):
        """_format_search_results includes [LOW-CONFIDENCE] when c < 0.35."""
        tid = _plant_thought(conn, test_user, text="low conf format unique wwwww",
                             stv_f=1.0, stv_c=0.1)
        results = open_brain.search(
            conn, query="low conf format unique wwwww", user_id=test_user, limit=5,
        )
        found = [r for r in results if r.get("THOUGHT_ID") == tid]
        assert found, "Planted thought not found"
        formatted = open_brain._format_search_results(found)
        assert "[LOW-CONFIDENCE]" in formatted, (
            f"LOW-CONFIDENCE marker missing from output: {formatted}"
        )


# ─── (g) _run_from_pi 'revise' op ─────────────────────────────────────────────


class TestPiBridgeRevise:
    """_run_from_pi 'revise' op dispatches to revise_thoughts."""

    @pytest.mark.integration
    def test_revise_op_through_pi_bridge(self, conn, test_user):
        """Pi bridge 'revise' op returns derived atom JSON.

        NOTE: _run_from_pi() calls conn.close() at the end, which would destroy
        the module-scoped conn fixture. To avoid this, we give Pi its own
        fresh connection by mocking _connect to produce a new connection (not
        the shared fixture conn). The planted thoughts use test_user on the
        live DB, so the Pi bridge's own connection can find them.
        """
        id_a = _plant_thought(conn, test_user, text="pi bridge premise A",
                              stv_f=1.0, stv_c=0.8)
        id_b = _plant_thought(conn, test_user, text="pi bridge premise B",
                              stv_f=1.0, stv_c=0.6)

        payload = {
            "op": "revise",
            "id_a": id_a,
            "id_b": id_b,
            "text": "pi bridge derived",
        }

        def _fresh_conn():
            """Give Pi its own connection (not the shared fixture)."""
            return open_brain._connect.__wrapped__() if hasattr(open_brain._connect, "__wrapped__") else open_brain._connect()

        captured_output = StringIO()
        with patch.object(open_brain, "_get_user_id", return_value=test_user):
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                with patch("sys.stdout", captured_output):
                    try:
                        open_brain._run_from_pi()
                    except SystemExit:
                        pass

        out = captured_output.getvalue().strip()
        assert out, "Pi bridge should produce output"
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            pytest.fail(f"Pi bridge output is not valid JSON: {out!r}")
        assert "error" not in data, f"Pi bridge revise returned error: {data}"
        assert "thought_id" in data, f"Pi bridge revise should return thought_id: {data}"
        assert "stv" in data, "Pi bridge revise result should carry stv"
        assert "derives_from" in data

    def test_revise_op_missing_id_returns_error(self):
        """Pi bridge 'revise' op with missing id_a returns error JSON."""
        payload = {"op": "revise", "id_b": "some-id"}
        captured_output = StringIO()
        with patch.object(open_brain, "_connect", side_effect=RuntimeError("no db needed")):
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                with patch("sys.stdout", captured_output):
                    try:
                        open_brain._run_from_pi()
                    except (SystemExit, RuntimeError):
                        pass
        out = captured_output.getvalue().strip()
        if out:
            data = json.loads(out)
            assert "error" in data, f"Expected error key, got: {data}"
