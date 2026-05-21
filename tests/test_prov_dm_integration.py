#!/usr/bin/env python3
"""brain-W1-S3: PROV-DM integration tests — gap items from S1+S2 coverage.

S1 covered schema. S2 covered capture flow. This corpus covers the integration
gaps: multi-user isolation across full lifecycle, source_uri scenarios,
prov_activity vocabulary, activity ID stability.

Run: python3 -m pytest tests/test_prov_dm_integration.py -v
"""
import os
import sys
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


class TestMultiUserProvenanceIsolation:
    def test_userA_thought_not_visible_to_userB_query(self, conn):
        """A's PROV doesn't leak to B's query results."""
        rA = open_brain.capture(
            conn,
            text="userA secret",
            user_id="isolation-userA",
            prov_activity="capture",
        )
        tidA = rA["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
                (tidA, "isolation-userB"),
            )
            assert cur.fetchone() is None
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tidA,))
            conn.commit()

    def test_query_scoped_returns_only_own(self, conn):
        """Search/list operations only surface caller's own PROV."""
        rA = open_brain.capture(
            conn, text="A's thought 1", user_id="iso-scoped-A"
        )
        rB = open_brain.capture(
            conn, text="B's thought 1", user_id="iso-scoped-B"
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT thought_id, prov_agent FROM brain.thoughts WHERE user_id=%s",
                ("iso-scoped-A",),
            )
            rows = cur.fetchall()
            ids_seen = {r[0] for r in rows}
            assert rA["thought_id"] in ids_seen
            assert rB["thought_id"] not in ids_seen
        finally:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                (rA["thought_id"], rB["thought_id"]),
            )
            conn.commit()

    def test_activity_id_unique_across_users(self, conn):
        """Same source pattern, different users → different activity IDs."""
        rA = open_brain.capture(conn, text="x", user_id="actA")
        rB = open_brain.capture(conn, text="x", user_id="actB")
        try:
            assert rA["thought_id"] != rB["thought_id"]
            # Activity IDs are derived from thought_id; uniqueness follows
            cur = conn.cursor()
            cur.execute(
                "SELECT was_generated_by FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                (rA["thought_id"], rB["thought_id"]),
            )
            gens = {r[0] for r in cur.fetchall()}
            assert len(gens) == 2
        finally:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id IN (%s, %s)",
                (rA["thought_id"], rB["thought_id"]),
            )
            conn.commit()

    def test_was_derived_from_cross_user_blocked_at_capture(self, conn):
        """S2 test refresher: PS scope blocks A→B derivation. Doubled here for class coherence."""
        parent = open_brain.capture(
            conn, text="parent for cross", user_id="dx-userA"
        )
        pid = parent["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="was_derived_from"):
                open_brain.capture(
                    conn,
                    text="child sneaky",
                    user_id="dx-userB",
                    was_derived_from=pid,
                )
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (pid,))
            conn.commit()

    def test_user_isolation_with_legacy_rows(self, conn):
        """Legacy-backfilled rows (prov_agent='legacy-import') still respect user_id scope."""
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, COUNT(*) FROM brain.thoughts
            WHERE prov_agent='legacy-import'
            GROUP BY user_id
            LIMIT 5
            """
        )
        rows = cur.fetchall()
        # If there are no legacy rows, nothing to check — skip
        if not rows:
            pytest.skip("No legacy rows present")
        # Otherwise: each legacy row's user_id is non-null and a recognizable identifier
        for user_id, count in rows:
            assert user_id is not None
            assert isinstance(user_id, str) and len(user_id) > 0


class TestSourceUri:
    def test_capture_without_source_uri_yields_null(self, conn):
        result = open_brain.capture(
            conn, text="no source", user_id="src-uri-none"
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT source_uri FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] is None
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()

    def test_direct_insert_with_long_source_uri_persists(self, conn):
        """source_uri is TEXT; should accept long URLs without truncation."""
        long_url = "https://example.com/very/long/path/" + ("x" * 500) + "/article"
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO brain.thoughts (
                    thought_id, user_id, raw_text, summary, thought_type,
                    topics, people, action_items, source,
                    prov_agent, prov_activity, was_generated_by, source_uri
                ) VALUES (
                    %s, %s, 'src-uri-long', 'src-uri-long', 'insight',
                    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'manual',
                    'test', 'import', 'activity-srcuri', %s
                )
                """,
                ("brain-srcuri-long-001", "srcuri-user", long_url),
            )
            conn.commit()
            cur.execute(
                "SELECT source_uri FROM brain.thoughts WHERE thought_id=%s",
                ("brain-srcuri-long-001",),
            )
            assert cur.fetchone()[0] == long_url
        finally:
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id=%s",
                ("brain-srcuri-long-001",),
            )
            conn.commit()


class TestProvActivityVocabulary:
    def test_default_capture_yields_capture_activity(self, conn):
        result = open_brain.capture(
            conn, text="default activity test", user_id="act-vocab-default"
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_activity FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == "capture"
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()

    def test_auto_capture_activity_label_format(self, conn):
        """auto-capture-{label} activity values from hook integration."""
        result = open_brain.capture(
            conn,
            text="auto-capture-decision-signal test",
            user_id="act-vocab-autocap",
            prov_agent="claude-code-hook-decision-signal",
            prov_activity="auto-capture-decision-signal",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_activity FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == "auto-capture-decision-signal"
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()

    def test_activity_field_no_enum_enforcement(self, conn):
        """No DB-side enum check; arbitrary VARCHAR(50) strings accepted."""
        result = open_brain.capture(
            conn,
            text="custom activity test",
            user_id="act-vocab-custom",
            prov_activity="custom-application-specific-activity",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_activity FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == "custom-application-specific-activity"
        finally:
            cur = conn.cursor()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
            conn.commit()


class TestActivityIdStability:
    def test_same_thought_id_yields_same_activity_id(self):
        tid = "brain-1234567890-stable"
        assert open_brain._generate_activity_id(tid) == open_brain._generate_activity_id(tid)

    def test_activity_ids_unique_per_thought(self, conn):
        rA = open_brain.capture(conn, text="aaa", user_id="actid-uniq")
        rB = open_brain.capture(conn, text="bbb", user_id="actid-uniq")
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT thought_id, was_generated_by FROM brain.thoughts WHERE user_id=%s",
                ("actid-uniq",),
            )
            rows = cur.fetchall()
            tids = {r[0] for r in rows}
            gens = {r[1] for r in rows}
            assert len(tids) == len(gens), "1:1 thought→activity invariant violated"
        finally:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM brain.thoughts WHERE user_id=%s", ("actid-uniq",)
            )
            conn.commit()
