#!/usr/bin/env python3
"""fblai-aulcu — RB completeness tests.

(a) register_skill creates a thought_versions snapshot (with
    prov_activity='skill_register') capturing the pre-stamp state BEFORE
    _stamp_skill_metadata flips thought_type to 'skill_ref'.

(b) After rollback_thought, the live brain.thoughts row carries
    prov_activity='rollback' so callers (search/recent/timeline) see the
    rollback event, not the original capture agent/activity.

Tests skip cleanly when DATABASE_URL is not available (psycopg2 missing
or no DB URL), matching the pattern used in test_register_skill.py.

Run: python3 -m pytest scripts/tests/test_rb_completeness.py -v
"""
import json
import os
import sys
import uuid

import pytest

# Add scripts dir to path so we can import open_brain.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Module-level skip guard ─────────────────────────────────────────────────


def _get_conn_or_skip():
    """Return a live connection or skip the module if none available."""
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
    cur = c.cursor()
    try:
        cur.execute("SET search_path TO brain, public")
        c.commit()
    finally:
        cur.close()
    return c


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    """Module-scoped live Postgres connection; skips if DB not available."""
    c = _get_conn_or_skip()
    yield c
    c.close()


def _cleanup_user(conn, uid: str) -> None:
    """Delete every row a test user wrote across the brain tables."""
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
        for tbl in (
            "brain.atom_links",
            "brain.replay_log",
            "brain.promotions",
        ):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        # thought_versions FK → thoughts: delete versions before the thought row.
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


@pytest.fixture()
def test_user(conn):
    """Unique test user id; cleans up its rows at teardown."""
    uid = f"test-rb-{uuid.uuid4().hex[:12]}"
    yield uid
    _cleanup_user(conn, uid)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _insert_thought(
    conn,
    user_id: str,
    text: str,
    thought_type: str = "insight",
) -> str:
    """Insert a minimal thought row (no embedding) for rollback tests."""
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
                NULL, '{}'::jsonb,
                NOW(), NOW()
            )
            """,
            (
                thought_id,
                user_id,
                text[:16384],
                text[:1000],
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


def _version_count(conn, thought_id: str) -> int:
    """Return the number of thought_versions rows for a given thought_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM brain.thought_versions WHERE thought_id = %s",
            (thought_id,),
        )
        return cur.fetchone()[0]
    finally:
        cur.close()


def _latest_version_row(conn, thought_id: str) -> dict:
    """Return the highest-revision thought_versions row as a dict, or {}."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT revision, thought_type, prov_activity, prov_agent
            FROM brain.thought_versions
            WHERE thought_id = %s
            ORDER BY revision DESC
            LIMIT 1
            """,
            (thought_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        return {
            "revision": row[0],
            "thought_type": row[1],
            "prov_activity": row[2],
            "prov_agent": row[3],
        }
    finally:
        cur.close()


def _live_thought_prov(conn, thought_id: str) -> dict:
    """Return prov_agent and prov_activity from the live brain.thoughts row."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT prov_agent, prov_activity FROM brain.thoughts WHERE thought_id = %s",
            (thought_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        return {"prov_agent": row[0], "prov_activity": row[1]}
    finally:
        cur.close()


# ─── Tests: (a) register_skill versions before stamp ─────────────────────────


class TestRegisterSkillVersionsBeforeStamp:
    """Verify that register_skill creates a thought_versions snapshot capturing
    the pre-stamp state (thought_type != 'skill_ref') before _stamp_skill_metadata
    overwrites the live row.
    """

    def test_register_skill_creates_thought_versions_row(self, conn, test_user):
        """After register_skill, at least one thought_versions row must exist
        for the new skill atom.
        """
        result = open_brain.register_skill(
            conn,
            name="rb-test-skill-a1",
            description=(
                "A test skill for RB completeness: verifies that a "
                "thought_versions row is created before the live row "
                "is stamped with skill_ref."
            ),
            user_id=test_user,
            prov_agent=f"test-agent-{test_user}",
        )
        skill_id = result["skill_id"]
        count = _version_count(conn, skill_id)
        assert count >= 1, (
            f"Expected ≥1 thought_versions row for skill {skill_id!r}, "
            f"got {count}. The snapshot before _stamp_skill_metadata is missing."
        )

    def test_register_skill_snapshot_has_skill_register_activity(self, conn, test_user):
        """The snapshot row created before the stamp must carry
        prov_activity='skill_register' so it is labelled for audit traceability.
        """
        result = open_brain.register_skill(
            conn,
            name="rb-test-skill-a2",
            description=(
                "A second test skill for RB completeness: verifies that the "
                "snapshot row carries prov_activity=skill_register."
            ),
            user_id=test_user,
            prov_agent=f"test-agent-{test_user}",
        )
        skill_id = result["skill_id"]
        ver = _latest_version_row(conn, skill_id)
        assert ver, f"No thought_versions row found for skill {skill_id!r}."
        assert ver["prov_activity"] == "skill_register", (
            f"Expected prov_activity='skill_register' on the snapshot row, "
            f"got {ver['prov_activity']!r}."
        )

    def test_register_skill_snapshot_captures_pre_stamp_thought_type(self, conn, test_user):
        """The snapshot row's thought_type must NOT be 'skill_ref' — it captures
        the state BEFORE _stamp_skill_metadata overwrites the live row.  After
        capture() returns, the LLM classifies the body as 'pattern' or 'insight';
        the snapshot locks that in before the stamp replaces it with 'skill_ref'.
        """
        result = open_brain.register_skill(
            conn,
            name="rb-test-skill-a3",
            description=(
                "Third test skill for RB completeness: verifies that the "
                "snapshot row preserves the pre-stamp thought_type, not "
                "skill_ref which is written only after the snapshot."
            ),
            user_id=test_user,
            prov_agent=f"test-agent-{test_user}",
        )
        skill_id = result["skill_id"]
        ver = _latest_version_row(conn, skill_id)
        assert ver, f"No thought_versions row found for skill {skill_id!r}."
        # The snapshot is taken BEFORE _stamp_skill_metadata fires.
        # If the snapshot were taken after, thought_type would be 'skill_ref'.
        assert ver["thought_type"] != "skill_ref", (
            f"Snapshot row carries thought_type='skill_ref', which means the "
            f"snapshot was taken AFTER the stamp — the RB-before-stamp ordering "
            f"is violated. thought_type should be the LLM-assigned type "
            f"(e.g. 'pattern' or 'insight') at snapshot time."
        )

    def test_register_skill_revision_incremented(self, conn, test_user):
        """The snapshot creates revision 1 (first version) for the new atom."""
        result = open_brain.register_skill(
            conn,
            name="rb-test-skill-a4",
            description=(
                "Fourth test skill: verifies the snapshot increments revision "
                "from zero (new atom) to 1."
            ),
            user_id=test_user,
            prov_agent=f"test-agent-{test_user}",
        )
        skill_id = result["skill_id"]
        ver = _latest_version_row(conn, skill_id)
        assert ver, f"No thought_versions row found for skill {skill_id!r}."
        assert ver["revision"] == 1, (
            f"Expected revision=1 for the first snapshot of a new skill atom, "
            f"got {ver['revision']}."
        )


# ─── Tests: (b) rollback_thought stamps prov on live row ─────────────────────


class TestRollbackThoughtProvStamping:
    """Verify that rollback_thought sets prov_activity='rollback' (and a
    non-null prov_agent) on the live brain.thoughts row so subsequent reads
    see the rollback event in the live row's PROV-DM fields.
    """

    def _make_versioned_thought(self, conn, user_id: str, text: str) -> str:
        """Insert a thought row and immediately snapshot it so rollback_thought
        has at least one version to roll back to.

        Returns the thought_id.
        """
        tid = _insert_thought(conn, user_id, text)
        open_brain.snapshot_thought(
            conn,
            thought_id=tid,
            user_id=user_id,
            prov_activity="capture",
        )
        return tid

    def test_rollback_sets_prov_activity_rollback_on_live_row(self, conn, test_user):
        """After rollback_thought(..., to_revision=1), the live brain.thoughts
        row must have prov_activity='rollback'.
        """
        tid = self._make_versioned_thought(
            conn, test_user, "RB live-row prov test thought."
        )
        prov_agent_str = f"test-rollback-agent-{test_user}"
        open_brain.rollback_thought(
            conn,
            thought_id=tid,
            user_id=test_user,
            to_revision=1,
            prov_agent=prov_agent_str,
        )
        live = _live_thought_prov(conn, tid)
        assert live.get("prov_activity") == "rollback", (
            f"Expected prov_activity='rollback' on the live row after "
            f"rollback_thought, got {live.get('prov_activity')!r}."
        )

    def test_rollback_sets_prov_agent_on_live_row(self, conn, test_user):
        """After rollback_thought, the live brain.thoughts row must have the
        prov_agent value passed to rollback_thought (not the original agent).
        """
        tid = self._make_versioned_thought(
            conn, test_user, "RB live-row prov_agent test."
        )
        prov_agent_str = f"test-rollback-agent-{test_user}"
        open_brain.rollback_thought(
            conn,
            thought_id=tid,
            user_id=test_user,
            to_revision=1,
            prov_agent=prov_agent_str,
        )
        live = _live_thought_prov(conn, tid)
        assert live.get("prov_agent") == prov_agent_str, (
            f"Expected prov_agent={prov_agent_str!r} on the live row after "
            f"rollback_thought, got {live.get('prov_agent')!r}."
        )

    def test_rollback_does_not_overwrite_prov_activity_with_original(
        self, conn, test_user
    ):
        """The live row's prov_activity after rollback must NOT be 'capture'
        (which was the original value), confirming the UPDATE actually fires.
        """
        tid = self._make_versioned_thought(
            conn, test_user, "RB prov_activity not-capture test."
        )
        open_brain.rollback_thought(
            conn,
            thought_id=tid,
            user_id=test_user,
            to_revision=1,
        )
        live = _live_thought_prov(conn, tid)
        assert live.get("prov_activity") != "capture", (
            f"Live row prov_activity is still 'capture' after rollback — "
            f"the prov stamping UPDATE did not fire correctly."
        )
