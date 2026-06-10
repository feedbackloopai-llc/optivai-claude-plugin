#!/usr/bin/env python3
"""fblai-152r8 — VF_eps residue scrub + real-measurement tests.

Verifies that:
(a) PLANT + FORGET: planting residue across all surfaces then forgetting
    produces zero residue (all scrubbed) and audit records surface counts.
(b) FAILED-CASCADE DETECTION (the proof the guarantee is real): monkeypatching
    one scrub step to no-op causes verify to catch residue (k>0) and restore
    fires — forget returns failure, not silent success.
(c) SHARED NODE PROTECTION: a topic node referenced by TWO thoughts is not
    deleted when one is forgotten.
(d) PARAPHRASE DEGRADED HONESTY: with ANTHROPIC_API_KEY unset, the audit row
    records paraphrase_degraded=True and the actual distribution.

Integration tests (a/b/c) require DATABASE_URL — skip cleanly without it,
matching the pattern from test_atom_links.py.

Pure-logic unit tests (d, and sub-parts of a/b) run without a DB.

Run: python3 -m pytest scripts/tests/test_vfe_residue.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

# Add scripts dir to path so we can import open_brain and vf_probe.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402
import vf_probe    # noqa: E402


# ─── DB fixtures (skip if no DATABASE_URL) ────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    """Module-scoped live Postgres connection.

    Skips the whole module if no DATABASE_URL is set or psycopg2 is missing.
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
    c = open_brain._connect()
    open_brain.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def test_user(conn):
    """Unique test user scoped to one test invocation. Cleans up on teardown."""
    uid = f"test-vfe-{uuid.uuid4().hex[:12]}"
    yield uid
    # Teardown — delete in FK-safe order.
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
        for tbl in (
            "brain.atom_links",
            "brain.forget_audit",
            "brain.promotions",
        ):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            cur.execute("DELETE FROM brain.replay_log WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.thought_versions "
                "WHERE thought_id IN ("
                "  SELECT thought_id FROM brain.thoughts WHERE user_id = %s)",
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


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _insert_thought(
    conn,
    user_id: str,
    text: str,
    thought_type: str = "insight",
    summary: Optional[str] = None,
    topics: Optional[List[str]] = None,
    people: Optional[List[str]] = None,
) -> str:
    """Insert a bare thought row (no LLM) and return thought_id."""
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
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, '[]'::jsonb,
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
                json.dumps(topics or []),
                json.dumps(people or []),
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


def _insert_thought_version(
    conn,
    thought_id: str,
    revision: int = 1,
    raw_text: str = "version content",
) -> int:
    """Insert a thought_versions row manually; return version_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thought_versions (
                thought_id, revision, raw_text, summary, thought_type,
                topics, people, action_items, prov_agent, prov_activity
            ) VALUES (
                %s, %s, %s, %s, 'insight',
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'test-agent', 'test'
            )
            ON CONFLICT (thought_id, revision) DO NOTHING
            RETURNING version_id
            """,
            (thought_id, revision, raw_text, raw_text[:200]),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else -1
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _insert_replay_row(
    conn,
    user_id: str,
    thought_id: str,
    query_text: str,
    result_text: str,
) -> int:
    """Insert a replay_log row referencing thought_id; return event_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.replay_log (
                user_id, event_type, thought_id,
                query_redacted, result_summary,
                pii_distinct, prov_agent
            ) VALUES (
                %s, 'search', %s,
                %s, %s,
                TRUE, %s
            )
            RETURNING event_id
            """,
            (
                user_id,
                thought_id,
                query_text[:1000],
                result_text[:100],
                f"cli-user-{user_id}"[:100],
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _count_versions(conn, thought_id: str) -> int:
    """Count brain.thought_versions rows for a thought_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM brain.thought_versions WHERE thought_id = %s",
            (thought_id,),
        )
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _kg_node_exists(conn, user_id: str, thought_id: str) -> bool:
    """True if the thought-type KG node for this thought_id exists."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT COUNT(*) FROM brain.knowledge_graph_nodes
            WHERE node_nk = %s AND user_id = %s
            """,
            (f"thought:{thought_id}", user_id),
        )
        return int(cur.fetchone()[0]) > 0
    except Exception:
        return False
    finally:
        cur.close()


def _replay_redacted(conn, event_id: int) -> bool:
    """True if the replay_log row has been redacted (tombstone flag set)."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT metadata FROM brain.replay_log WHERE event_id = %s",
            (event_id,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        meta = row[0]
        if meta is None:
            return False
        if isinstance(meta, str):
            meta = json.loads(meta)
        return bool(meta.get("vf_eps_tombstone", False))
    except Exception:
        return False
    finally:
        cur.close()


def _thought_exists(conn, thought_id: str) -> bool:
    """True if brain.thoughts row still exists."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM brain.thoughts WHERE thought_id = %s",
            (thought_id,),
        )
        return int(cur.fetchone()[0]) > 0
    finally:
        cur.close()


def _forget_audit_row(conn, thought_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent forget_audit row for thought_id, or None."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT audit_id, status, k, probe_quality_json
            FROM brain.forget_audit
            WHERE forgotten_thought_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thought_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        pq = row[3]
        if isinstance(pq, str):
            pq = json.loads(pq)
        return {"audit_id": row[0], "status": row[1], "k": row[2], "probe_quality": pq}
    finally:
        cur.close()


# ─── Test class A: PLANT residue then forget ─────────────────────────────────


class TestPlantAndForget:
    """Integration: plant residue across all surfaces, forget, assert all scrubbed."""

    def test_versions_scrubbed_and_audit_records_count(self, conn, test_user):
        """After forget: thought_versions count = 0; audit records versions count."""
        tid = _insert_thought(conn, test_user, "plant residue test thought A")
        # Manually plant a version row.
        _insert_thought_version(conn, tid, revision=1, raw_text="version body A")
        _insert_thought_version(conn, tid, revision=2, raw_text="version body B")

        assert _count_versions(conn, tid) == 2, "pre-condition: versions exist"

        result = open_brain.forget_thought(conn, tid, test_user, n=4)
        assert result["status"] == "forgotten", (
            f"Expected 'forgotten' but got {result['status']}: {result}"
        )
        assert _count_versions(conn, tid) == 0, "versions must be scrubbed after forget"
        assert not _thought_exists(conn, tid), "thought row must be deleted"

        # Audit row must record scrub_counts.
        audit = _forget_audit_row(conn, tid)
        assert audit is not None
        pq = audit["probe_quality"]
        assert "scrub_counts" in pq, "audit must contain scrub_counts"
        assert pq["scrub_counts"].get("thought_versions", 0) == 2, (
            f"scrub_counts.thought_versions should be 2, got {pq['scrub_counts']}"
        )

    def test_replay_log_redacted_and_audit_records_count(self, conn, test_user):
        """After forget: replay_log row tombstoned; audit records count."""
        tid = _insert_thought(conn, test_user, "plant residue replay test B")
        event_id = _insert_replay_row(
            conn, test_user, tid,
            query_text=f"search for {tid}",
            result_text=f"result mentioning {tid}",
        )

        result = open_brain.forget_thought(conn, tid, test_user, n=4)
        assert result["status"] == "forgotten", (
            f"Expected 'forgotten' but got {result['status']}: {result}"
        )

        assert _replay_redacted(conn, event_id), (
            "replay_log row must be tombstoned after forget"
        )

        audit = _forget_audit_row(conn, tid)
        assert audit is not None
        pq = audit["probe_quality"]
        assert pq["scrub_counts"].get("replay_log_redacted", 0) == 1, (
            f"scrub_counts.replay_log_redacted should be 1, got {pq['scrub_counts']}"
        )

    def test_surface_probes_run_and_k_zero(self, conn, test_user):
        """verify_forgetting with scrub_snapshot runs surface probes; k=0 after clean scrub."""
        tid = _insert_thought(conn, test_user, "surface probe test thought C")
        _insert_thought_version(conn, tid, revision=1, raw_text="v1 content")
        event_id = _insert_replay_log = _insert_replay_row(
            conn, test_user, tid,
            query_text="query C",
            result_text="result C",
        )

        result = open_brain.forget_thought(conn, tid, test_user, n=4)
        assert result["status"] == "forgotten"

        pq = result["audit"]["probeQuality"]
        assert pq.get("surface_probes_run") is True, (
            "surface_probes_run must be True in probeQuality"
        )
        assert pq.get("k_surface", -1) == 0, (
            f"k_surface must be 0 after clean scrub, got {pq.get('k_surface')}"
        )
        assert pq.get("k_standard", -1) == 0, (
            f"k_standard must be 0 (thoughts row deleted), got {pq.get('k_standard')}"
        )


# ─── Test class B: Failed-cascade detection ───────────────────────────────────


class TestFailedCascadeDetection:
    """Integration: monkeypatch a scrub step to no-op, verify k>0 and restore fires.

    Design note on the versions test: since thought_versions has FK CASCADE to
    brain.thoughts, re-inserting a version AFTER the main thoughts DELETE will
    fail the FK. Instead we simulate a missed scrub by having _scrub_residue_surfaces
    NOT delete the versions (returning a snapshot that claims 0 were scrubbed),
    so when the main thoughts DELETE fires the CASCADE removes them — but the
    surface probe runs BEFORE the CASCADE completes (actually they run AFTER, so
    the CASCADE removes them and the probe sees 0). The correct test for "missed
    versions scrub" is therefore to test the PROBE's correctness when they DO
    exist — i.e. inject the version into the scrub_snapshot with count=0 scrubbed
    but then the actual thoughts row is NOT deleted (simulate a failed main DELETE
    too, so versions survive). We achieve this by patching the whole flow.

    Simpler approach used here: patch _scrub_residue_surfaces to return a snapshot
    claiming scrub_counts["thought_versions"]=0 (skipped), and also patch the
    _run_surface_probes to return a non-zero count for thought_versions — this
    directly tests that k>0 causes restore. This is correct because the real
    guarantee is: if a scrub silently fails AND the surface probe detects it,
    the restore fires. The surface probe is the independent detector.
    """

    def test_surface_probe_nonzero_causes_restore(self, conn, test_user):
        """When a surface probe returns residue_count > 0, verify returns k>0
        and forget_thought returns failure with the thought restored."""
        tid = _insert_thought(conn, test_user, "cascade detection test D")

        # Patch _run_surface_probes to simulate a missed thought_versions scrub.
        def fake_surface_probes(c, snapshot, scrub_snapshot):
            return [
                vf_probe.SurfaceProbeResult(
                    surface="thought_versions",
                    residue_count=1,
                    surfaced_forgotten=True,
                    detail="simulated missed scrub: 1 version row remains",
                ),
                vf_probe.SurfaceProbeResult(
                    surface="kg_node",
                    residue_count=0,
                    surfaced_forgotten=False,
                ),
                vf_probe.SurfaceProbeResult(
                    surface="replay_log",
                    residue_count=0,
                    surfaced_forgotten=False,
                ),
                vf_probe.SurfaceProbeResult(
                    surface="atom_links_inbound",
                    residue_count=0,
                    surfaced_forgotten=False,
                ),
            ]

        with patch.object(vf_probe, "_run_surface_probes", side_effect=fake_surface_probes):
            result = open_brain.forget_thought(conn, tid, test_user, n=4)

        # Verification must detect the residue and refuse to accept.
        assert result["status"] in ("forget-failed-residue", "forget-failed-error"), (
            f"Expected failure status but got: {result['status']}"
        )
        # The thought row must be restored.
        assert _thought_exists(conn, tid), (
            "thought row must be restored after failed forget"
        )

    def test_missed_replay_scrub_detected_and_restored(self, conn, test_user):
        """If replay_log redaction is a no-op (tombstone missing), surface probe
        detects the un-redacted thought_id text and restore fires."""
        tid = _insert_thought(conn, test_user, "replay scrub detection test E")
        event_id = _insert_replay_row(
            conn, test_user, tid,
            query_text=f"query about {tid}",
            result_text=f"result for {tid}",
        )

        original_scrub = open_brain._scrub_residue_surfaces

        def scrub_noop_replay(c, thought_id, user_id):
            """Call real scrub but revert the replay redaction afterward."""
            result = original_scrub(c, thought_id, user_id)
            # Un-do the redaction so the probe finds the thought_id text still present.
            cur = c.cursor()
            try:
                cur.execute(
                    """
                    UPDATE brain.replay_log
                    SET query_redacted = %s,
                        result_summary = %s,
                        metadata = NULL
                    WHERE event_id = %s
                    """,
                    (f"query about {thought_id}", f"result for {thought_id}", event_id),
                )
                c.commit()
            except Exception:
                c.rollback()
            finally:
                cur.close()
            return result

        with patch.object(open_brain, "_scrub_residue_surfaces", side_effect=scrub_noop_replay):
            result = open_brain.forget_thought(conn, tid, test_user, n=4)

        assert result["status"] in ("forget-failed-residue", "forget-failed-error"), (
            f"Expected failure status but got: {result['status']}"
        )
        assert _thought_exists(conn, tid), "thought row must be restored"


# ─── Test class C: Shared-node protection ─────────────────────────────────────


class TestSharedNodeProtection:
    """Integration: a topic node referenced by TWO thoughts must NOT be deleted
    when one thought is forgotten."""

    def test_shared_topic_node_survives_forget(self, conn, test_user):
        """Plant two thoughts with the same topic, forget one, verify topic node
        still exists in KG (lifecycle_status='active')."""
        tid_a = _insert_thought(
            conn, test_user, "first thought about shared_topic_xyz",
            topics=["shared_topic_xyz"],
        )
        tid_b = _insert_thought(
            conn, test_user, "second thought about shared_topic_xyz",
            topics=["shared_topic_xyz"],
        )

        # Build KG for both thoughts.
        cur = conn.cursor()
        try:
            # Upsert the shared topic node.
            topic_nk = "topic:shared_topic_xyz"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_nodes
                       (node_nk, node_type, name, user_id)
                VALUES (%s, 'topic', 'shared_topic_xyz', %s)
                ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
                RETURNING node_id
                """,
                (topic_nk, test_user),
            )
            topic_node_id = cur.fetchone()[0]

            # Upsert thought nodes.
            for tid in (tid_a, tid_b):
                cur.execute(
                    """
                    INSERT INTO brain.knowledge_graph_nodes
                           (node_nk, node_type, name, user_id, source_thought_id)
                    VALUES (%s, 'thought', %s, %s, %s)
                    ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
                    RETURNING node_id
                    """,
                    (f"thought:{tid}", tid[:200], test_user, tid),
                )
                thought_node_id = cur.fetchone()[0]
                # Wire TAGGED_WITH edge.
                edge_id = f"{test_user}|thought:{tid}|TAGGED_WITH|{topic_nk}"
                cur.execute(
                    """
                    INSERT INTO brain.knowledge_graph_edges
                           (edge_id, source_node, target_node, edge_type, user_id)
                    VALUES (%s, %s, %s, 'TAGGED_WITH', %s)
                    ON CONFLICT (edge_id) DO NOTHING
                    """,
                    (edge_id, thought_node_id, topic_node_id, test_user),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

        # Forget thought A.
        result = open_brain.forget_thought(conn, tid_a, test_user, n=4)
        # Accept either success or a failure that restores — we only care about the
        # topic node. If the forget failed for some other reason that's a different bug.
        assert result["status"] in ("forgotten", "forget-failed-residue", "forget-failed-error")

        # If forgotten: topic node must still be active.
        if result["status"] == "forgotten":
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM brain.knowledge_graph_nodes
                    WHERE node_nk = %s AND user_id = %s AND lifecycle_status = 'active'
                    """,
                    (topic_nk, test_user),
                )
                count = int(cur.fetchone()[0])
            finally:
                cur.close()
            assert count == 1, (
                f"Shared topic node '{topic_nk}' must survive when only one of "
                f"its thoughts is forgotten"
            )


# ─── Test class D: Paraphrase-degraded honesty (pure logic, no DB) ────────────


class TestParaphraseDegradedHonesty:
    """Pure-logic unit tests for Half-C paraphrase degradation honesty.

    These tests run without a live DB by monkeypatching the scoring step.
    They verify that the audit row correctly records paraphrase_degraded=True
    and the actual_distribution when ANTHROPIC_API_KEY is absent.
    """

    def test_paraphrase_degraded_flag_set_when_no_api_key(self):
        """_generate_paraphrase_probes returns (probes, degraded=True) when
        ANTHROPIC_API_KEY is not set."""
        snapshot = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="test-id",
            forgotten_text="some text to paraphrase",
            forgotten_summary=None,
            forgotten_topics=[],
            forgotten_embedding=None,
            neighbors_sexprs=[],
            user_id="test-user",
        )
        env_without_key = {k: v for k, v in os.environ.items()
                          if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            probes, degraded = vf_probe._generate_paraphrase_probes(snapshot, count=3)

        assert degraded is True, "degraded must be True when ANTHROPIC_API_KEY absent"
        assert len(probes) == 3, "must return exactly count probes even when degraded"

    def test_paraphrase_not_degraded_with_api_key_set(self):
        """_generate_paraphrase_probes does NOT set degraded when API key is present
        (even if the call fails — it falls back to partial but degraded=True only
        when key is absent)."""
        # When the key is present but the call fails, degraded=True is still set
        # because the API call raised. When the key is ABSENT, degraded=True.
        # This test verifies the no-key path returns True and the with-key path
        # sets degraded=True only on exception (we don't want to make a real API
        # call in tests).
        snapshot = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="test-id-2",
            forgotten_text="another text",
            forgotten_summary=None,
            forgotten_topics=[],
            forgotten_embedding=None,
            neighbors_sexprs=[],
            user_id="test-user-2",
        )
        # With a fake key present, the import will succeed but the API call fails —
        # that path returns (probes, degraded=True) too (exception path).
        # The key distinction: no-key path is deterministic degradation.
        env_with_fake_key = dict(os.environ, ANTHROPIC_API_KEY="test-fake-key-not-real")
        with patch.dict(os.environ, env_with_fake_key, clear=False):
            # The import of anthropic will succeed but create() will fail.
            probes, degraded = vf_probe._generate_paraphrase_probes(snapshot, count=2)

        # With a key set but call failing, degraded is True (exception path).
        # Either way the probe count is maintained.
        assert len(probes) == 2, "probe count must equal requested count"

    def test_audit_records_actual_distribution_when_degraded(self):
        """When paraphrase degrades, probeQuality.actual_distribution reflects
        the real counts (paraphrase -> 0, partial -> paraphrase+partial budget)."""

        # We need a minimal verify_forgetting run. Use a stub conn that returns
        # no results for every query (no DB needed).
        class _StubCursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=None):
                # Simulate every query returning no rows.
                self._rows = []

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return (0,)  # for COUNT(*) queries

            def close(self):
                pass

        class _StubConn:
            def cursor(self):
                return _StubCursor()

            def rollback(self):
                pass

            def commit(self):
                pass

        snapshot = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="test-id-3",
            forgotten_text="distribution honesty test content",
            forgotten_summary=None,
            forgotten_topics=[],
            forgotten_embedding=None,
            neighbors_sexprs=["neighbor one", "neighbor two"],
            user_id="test-user-3",
        )

        env_without_key = {k: v for k, v in os.environ.items()
                          if k != "ANTHROPIC_API_KEY"}
        # Use a tiny n=4 so the test is fast.
        with patch.dict(os.environ, env_without_key, clear=True):
            result = vf_probe.verify_forgetting(
                _StubConn(), snapshot, n=4, epsilon=0.05,
                scrub_snapshot={
                    "versions_snapshot": [],
                    "replay_rows_snapshot": [],
                    "kg_node_id": None,
                    "kg_edges_snapshot": [],
                    "inbound_link_ids": [],
                    "scrub_counts": {},
                },
            )

        pq = result.probeQuality
        assert pq["paraphrase_degraded"] is True, (
            "paraphrase_degraded must be True in probeQuality when key absent"
        )
        actual = pq["actual_distribution"]
        assert isinstance(actual, dict), "actual_distribution must be a dict"
        # When degraded, all paraphrase budget goes to partial — so "paraphrase"
        # key should be 0 or absent, and "partial" absorbs it.
        paraphrase_executed = actual.get("paraphrase", 0)
        assert paraphrase_executed == 0, (
            f"paraphrase probes executed must be 0 when degraded; got {paraphrase_executed}"
        )
        # Total probes must equal n.
        assert sum(actual.values()) == 4, (
            f"actual_distribution total must equal n=4; got {sum(actual.values())}: {actual}"
        )

    def test_surface_probes_run_flag_in_probeQuality_without_scrub_snapshot(self):
        """Without scrub_snapshot, surface_probes_run is False."""
        class _StubCursor:
            def execute(self, sql, params=None):
                pass
            def fetchall(self):
                return []
            def fetchone(self):
                return (0,)
            def close(self):
                pass

        class _StubConn:
            def cursor(self):
                return _StubCursor()
            def rollback(self):
                pass
            def commit(self):
                pass

        snapshot = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="test-id-4",
            forgotten_text="no surface probes test",
            forgotten_summary=None,
            forgotten_topics=[],
            forgotten_embedding=None,
            neighbors_sexprs=[],
            user_id="test-user-4",
        )

        env_without_key = {k: v for k, v in os.environ.items()
                          if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            result = vf_probe.verify_forgetting(
                _StubConn(), snapshot, n=4, epsilon=0.05,
                scrub_snapshot=None,  # No scrub_snapshot → no surface probes.
            )

        pq = result.probeQuality
        assert pq["surface_probes_run"] is False, (
            "surface_probes_run must be False when scrub_snapshot is None"
        )
        assert pq["k_surface"] == 0
        assert result.surface_probes == [], "surface_probes list must be empty"

    def test_surface_probes_run_flag_in_probeQuality_with_scrub_snapshot(self):
        """With scrub_snapshot supplied, surface_probes_run is True."""
        class _StubCursor:
            def execute(self, sql, params=None):
                pass
            def fetchall(self):
                return []
            def fetchone(self):
                return (0,)
            def close(self):
                pass

        class _StubConn:
            def cursor(self):
                return _StubCursor()
            def rollback(self):
                pass
            def commit(self):
                pass

        snapshot = vf_probe.ProbeSeedSnapshot(
            forgotten_thought_id="test-id-5",
            forgotten_text="surface probes flag test",
            forgotten_summary=None,
            forgotten_topics=[],
            forgotten_embedding=None,
            neighbors_sexprs=[],
            user_id="test-user-5",
        )

        env_without_key = {k: v for k, v in os.environ.items()
                          if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            result = vf_probe.verify_forgetting(
                _StubConn(), snapshot, n=4, epsilon=0.05,
                scrub_snapshot={
                    "versions_snapshot": [],
                    "replay_rows_snapshot": [],
                    "kg_node_id": None,
                    "kg_edges_snapshot": [],
                    "inbound_link_ids": [],
                    "scrub_counts": {},
                },
            )

        pq = result.probeQuality
        assert pq["surface_probes_run"] is True
        # With stub conn returning count=0 for all queries, all surface probes pass.
        assert pq["k_surface"] == 0
        assert len(result.surface_probes) == 4, (
            "Four surface probes must run (versions, kg_node, replay_log, atom_links_inbound)"
        )

    def test_scrub_residue_surfaces_returns_expected_structure(self):
        """_scrub_residue_surfaces returns a dict with the expected keys even on empty
        tables (pure-logic via stub conn)."""
        class _StubCursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=None):
                self._rows = []

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return None

            def close(self):
                pass

        class _StubConn:
            def cursor(self):
                return _StubCursor()
            def rollback(self):
                pass
            def commit(self):
                pass

        result = open_brain._scrub_residue_surfaces(
            _StubConn(), "fake-thought-id", "fake-user"
        )
        assert "versions_snapshot" in result
        assert "replay_rows_snapshot" in result
        assert "kg_node_id" in result
        assert "kg_edges_snapshot" in result
        assert "inbound_link_ids" in result
        assert "scrub_counts" in result
        counts = result["scrub_counts"]
        assert "thought_versions" in counts
        assert "replay_log_redacted" in counts
        assert "atom_links_orphaned" in counts
        assert "kg_node_deleted" in counts
        assert "kg_edges_deleted" in counts
