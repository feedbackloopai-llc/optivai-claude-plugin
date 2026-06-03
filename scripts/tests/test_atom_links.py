#!/usr/bin/env python3
"""gz-0l68v — Connected provenance graph (atom_links) tests.

Verifies the typed-link layer over ``brain.thoughts``:
  - schema creation is idempotent
  - LINK_TYPES is a closed CLI-validated set
  - --capture --link writes link rows with PROV-DM stamps
  - invalid link types reject the whole capture
  - --add-link and --show-links work post-hoc
  - --query-unresolved-findings excludes finding atoms with incoming
    'resolves' links and includes those without
  - --query-orphan-links flags genuinely-dangling targets but not bead-ids
  - VF_eps forget of a source atom cascades to its outgoing links
  - tenant isolation is preserved on link queries

Each test uses an isolated TEST USER ID and cleans up its own rows so the
real user's brain is unaffected. Tests are skipped (not failed) when no
DATABASE_URL is configured.

Run: python3 -m pytest scripts/tests/test_atom_links.py -v
"""
import json
import os
import sys
import uuid
from typing import List, Optional

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────


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
    # Make sure schema is in place — idempotent.
    open_brain.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def test_user(conn):
    """Unique test user id scoped to this test invocation.

    Yields the user id, then deletes every row this user wrote at teardown.
    """
    uid = f"test-link-{uuid.uuid4().hex[:12]}"
    yield uid
    # Cleanup — order matters: links FK source -> thoughts, so links
    # cascade when thoughts are deleted, but explicit DELETE is safer
    # if the FK is ever loosened.
    cur = conn.cursor()
    try:
        # Knowledge-graph artefacts created by _update_graph_incremental.
        for tbl in ("brain.kg_edges", "brain.kg_nodes",
                    "brain.knowledge_graph_edges", "brain.knowledge_graph_nodes"):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            cur.execute("DELETE FROM brain.atom_links WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.replay_log WHERE user_id = %s",
                (uid,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.promotions WHERE user_id = %s",
                (uid,),
            )
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


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _insert_thought(
    conn,
    user_id: str,
    text: str,
    thought_type: str = "insight",
    summary: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert a thought row directly (bypassing LLM metadata extraction) and
    return the thought_id. Used for fast test setup."""
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
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                'test', '', '',
                %s, 'capture', %s, NULL, NULL,
                NULL, %s::jsonb,
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
                json.dumps(metadata) if metadata else None,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return thought_id


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestSchemaAndConstants:
    def test_schema_creates_atom_links_table_idempotent(self, conn):
        # Run init twice; second call must not raise and the table must
        # remain in place.
        msg1 = open_brain.init_schema(conn)
        assert "Schema initialized successfully" in msg1 or "fallback" in msg1.lower()
        msg2 = open_brain.init_schema(conn)
        assert "Schema initialized successfully" in msg2 or "fallback" in msg2.lower()

        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'brain' AND table_name = 'atom_links'
                """
            )
            assert cur.fetchone() is not None, "atom_links table missing after init"
        finally:
            cur.close()

    def test_link_types_constant_is_closed_set(self):
        expected = {
            "derives_from",
            "rationale_for",
            "alternative_rejected_by",
            "verifies",
            "refutes",
            "resolves",
            "supersedes",
            "references_bead",
            "cites",
            "contradicts",
        }
        assert expected == open_brain.LINK_TYPES


class TestCaptureWithLinks:
    """Tests for --link flag during capture. We invoke add_link directly
    (bypassing the slow LLM metadata extraction) to keep the suite fast
    while still exercising the same code path the CLI hits."""

    def test_capture_with_single_link_writes_row(self, conn, test_user):
        # Plant a target atom + a source atom, then add one link between.
        target = _insert_thought(conn, test_user, "the target atom")
        source = _insert_thought(conn, test_user, "the source atom")

        result = open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="resolves",
            user_id=test_user,
            via="capture-flag",
        )
        assert result["created"] is True
        assert result["link_id"] > 0

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT source_id, target_id, link_type, user_id "
                "FROM brain.atom_links WHERE link_id = %s",
                (result["link_id"],),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row == (source, target, "resolves", test_user)

    def test_capture_with_multiple_links_writes_multiple_rows(self, conn, test_user):
        # Three targets, three different link types.
        source = _insert_thought(conn, test_user, "the source atom")
        t1 = _insert_thought(conn, test_user, "target 1")
        t2 = _insert_thought(conn, test_user, "target 2")
        t3 = _insert_thought(conn, test_user, "target 3")

        for tgt, lt in [(t1, "verifies"), (t2, "cites"), (t3, "supersedes")]:
            r = open_brain.add_link(
                conn,
                source_id=source,
                target_id=tgt,
                link_type=lt,
                user_id=test_user,
            )
            assert r["created"] is True

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links "
                "WHERE source_id = %s AND user_id = %s",
                (source, test_user),
            )
            count = cur.fetchone()[0]
        finally:
            cur.close()
        assert count == 3

    def test_capture_with_invalid_link_type_rejects(self, conn, test_user):
        # _parse_link_spec is the CLI-side validation gate. It must reject
        # an unknown link_type with a clear error AND list the allowed set.
        with pytest.raises(ValueError) as exc:
            open_brain._parse_link_spec("brain-1234:bogus_type")
        msg = str(exc.value).lower()
        assert "unknown link_type" in msg
        assert "bogus_type" in msg
        assert "allowed" in msg

        # add_link directly also rejects (defense-in-depth) — the gate is
        # both at CLI parse and at the writer.
        source = _insert_thought(conn, test_user, "source")
        with pytest.raises(ValueError):
            open_brain.add_link(
                conn,
                source_id=source,
                target_id="brain-target",
                link_type="bogus_type",
                user_id=test_user,
            )
        # And no row was written.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()

    def test_capture_with_bead_target_writes_link_no_fk_check(self, conn, test_user):
        # A bead-id target (gz-...) is NOT in brain.thoughts. The link
        # must still be writable — that's the whole point of the no-FK
        # design choice.
        source = _insert_thought(conn, test_user, "atom referencing a bead")
        bead_id = "gz-abc123"
        result = open_brain.add_link(
            conn,
            source_id=source,
            target_id=bead_id,
            link_type="references_bead",
            user_id=test_user,
        )
        assert result["created"] is True

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT target_id FROM brain.atom_links WHERE link_id = %s",
                (result["link_id"],),
            )
            assert cur.fetchone()[0] == bead_id
        finally:
            cur.close()


class TestPostHocAddLink:
    def test_post_hoc_add_link(self, conn, test_user):
        source = _insert_thought(conn, test_user, "source for post-hoc link")
        target = _insert_thought(conn, test_user, "target for post-hoc link")
        result = open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="cites",
            user_id=test_user,
            via="post-hoc",
        )
        assert result["created"] is True

    def test_post_hoc_add_link_to_missing_source_errors(self, conn, test_user):
        # Source does not exist in brain.thoughts under this user.
        with pytest.raises(RuntimeError) as exc:
            open_brain.add_link(
                conn,
                source_id="brain-does-not-exist-1234",
                target_id="brain-target-9999",
                link_type="resolves",
                user_id=test_user,
                via="post-hoc",
            )
        assert "does not exist" in str(exc.value).lower()
        # No row was written.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()


class TestShowLinks:
    def test_show_links_returns_outgoing_and_incoming(self, conn, test_user):
        # A -> resolves -> B.
        a = _insert_thought(conn, test_user, "atom A")
        b = _insert_thought(conn, test_user, "atom B")
        open_brain.add_link(
            conn,
            source_id=a,
            target_id=b,
            link_type="resolves",
            user_id=test_user,
        )

        a_links = open_brain.show_links(conn, atom_id=a, user_id=test_user)
        assert any(
            lr["link_type"] == "resolves" and lr["target_id"] == b
            for lr in a_links["outgoing"]
        )
        assert a_links["incoming"] == []  # A has no inbound

        b_links = open_brain.show_links(conn, atom_id=b, user_id=test_user)
        assert b_links["outgoing"] == []  # B has no outbound
        assert any(
            lr["link_type"] == "resolves" and lr["source_id"] == a
            for lr in b_links["incoming"]
        )

    def test_show_links_target_kind_classification(self, conn, test_user):
        source = _insert_thought(conn, test_user, "source with mixed targets")
        atom_target = _insert_thought(conn, test_user, "the atom target")
        bead_target = "gz-feedback-99"
        junk_target = "totally-not-an-id-1234"

        for tgt, lt in [
            (atom_target, "verifies"),
            (bead_target, "references_bead"),
            (junk_target, "cites"),
        ]:
            open_brain.add_link(
                conn,
                source_id=source,
                target_id=tgt,
                link_type=lt,
                user_id=test_user,
            )

        result = open_brain.show_links(conn, atom_id=source, user_id=test_user)
        by_target = {lr["target_id"]: lr for lr in result["outgoing"]}
        assert by_target[atom_target]["target_kind"] == "atom"
        assert by_target[bead_target]["target_kind"] == "bead"
        assert by_target[junk_target]["target_kind"] == "unknown"


class TestUnresolvedFindingsQuery:
    def test_query_unresolved_findings(self, conn, test_user):
        # Two finding-like atoms; one has an incoming resolves link, the
        # other does not. The query should return only the unresolved one.
        unresolved = _insert_thought(
            conn, test_user,
            "Discovered a bug in the auth flow — needs investigation",
            thought_type="sentinel_relevant",
            summary="bug in auth flow",
        )
        resolved = _insert_thought(
            conn, test_user,
            "Found a finding in the rendering layer (now closed)",
            thought_type="sentinel_relevant",
            summary="finding in rendering",
        )
        resolution = _insert_thought(
            conn, test_user,
            "Patched the rendering bug",
            thought_type="insight",
            summary="rendering patch",
        )
        open_brain.add_link(
            conn,
            source_id=resolution,
            target_id=resolved,
            link_type="resolves",
            user_id=test_user,
        )

        rows = open_brain.query_unresolved_findings(conn, user_id=test_user, limit=50)
        ids = [r["thought_id"] for r in rows]
        assert unresolved in ids
        assert resolved not in ids


class TestOrphanLinksQuery:
    def test_query_orphan_links(self, conn, test_user):
        source = _insert_thought(conn, test_user, "source with mixed targets")
        atom_target = _insert_thought(conn, test_user, "real target atom")
        bead_target = "gz-something"
        junk_target = f"some-junk-{uuid.uuid4().hex[:8]}"

        # Atom target — not an orphan (the target exists).
        open_brain.add_link(
            conn, source_id=source, target_id=atom_target,
            link_type="verifies", user_id=test_user,
        )
        # Bead target — not an orphan (gz- prefix excluded).
        open_brain.add_link(
            conn, source_id=source, target_id=bead_target,
            link_type="references_bead", user_id=test_user,
        )
        # Junk target — orphan.
        open_brain.add_link(
            conn, source_id=source, target_id=junk_target,
            link_type="cites", user_id=test_user,
        )

        rows = open_brain.query_orphan_links(conn, user_id=test_user, limit=100)
        target_ids = [r["target_id"] for r in rows]
        assert junk_target in target_ids
        assert atom_target not in target_ids
        assert bead_target not in target_ids


class TestProvStamping:
    def test_link_prov_stamped(self, conn, test_user):
        source = _insert_thought(conn, test_user, "source for prov stamp test")
        target = _insert_thought(conn, test_user, "target for prov stamp test")
        result = open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="rationale_for",
            user_id=test_user,
            via="post-hoc",
            prov_agent="test-agent-prov",
        )
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT prov FROM brain.atom_links WHERE link_id = %s",
                (result["link_id"],),
            )
            prov_raw = cur.fetchone()[0]
        finally:
            cur.close()
        # psycopg2 may return jsonb as dict or as str depending on type
        # registration. Normalize.
        if isinstance(prov_raw, str):
            prov = json.loads(prov_raw)
        else:
            prov = prov_raw
        assert prov is not None
        assert prov.get("agent") == "test-agent-prov"
        assert prov.get("activity") == "link_add"
        assert prov.get("via") == "post-hoc"
        # createdAt is an ISO-8601 timestamp string
        created = prov.get("createdAt")
        assert isinstance(created, str) and "T" in created


class TestUniqueAndCascade:
    def test_link_unique_constraint(self, conn, test_user):
        # Same (source, target, type, user) tuple inserted twice. We use
        # ON CONFLICT DO NOTHING so the second insert is an idempotent
        # no-op. The first call returns created=True; the second returns
        # created=False with the SAME link_id.
        source = _insert_thought(conn, test_user, "source for uniqueness test")
        target = _insert_thought(conn, test_user, "target for uniqueness test")

        r1 = open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="cites",
            user_id=test_user,
        )
        r2 = open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="cites",
            user_id=test_user,
        )
        assert r1["created"] is True
        assert r2["created"] is False
        assert r1["link_id"] == r2["link_id"]

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links "
                "WHERE source_id = %s AND target_id = %s AND link_type = %s "
                "AND user_id = %s",
                (source, target, "cites", test_user),
            )
            assert cur.fetchone()[0] == 1
        finally:
            cur.close()

    def test_link_cascade_on_source_forget(self, conn, test_user):
        # Create A with outgoing link to B. Delete A directly (simulating
        # the post-VF_eps DELETE on brain.thoughts). The atom_links FK
        # with ON DELETE CASCADE must remove the link row.
        #
        # We don't actually invoke forget_thought() here — it runs a 300-
        # probe verification loop that takes ~10s and depends on probe
        # quality. The cascade behavior is enforced at the FK level
        # regardless of HOW the row is deleted (forget_thought does a
        # plain DELETE after probe verification).
        a = _insert_thought(conn, test_user, "atom A to be forgotten")
        b = _insert_thought(conn, test_user, "atom B will be orphaned")
        lr = open_brain.add_link(
            conn,
            source_id=a,
            target_id=b,
            link_type="resolves",
            user_id=test_user,
        )
        link_id = lr["link_id"]

        cur = conn.cursor()
        try:
            # Sanity — the link is there.
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE link_id = %s",
                (link_id,),
            )
            assert cur.fetchone()[0] == 1

            # Delete A from thoughts. Clean up dependent rows first that
            # don't have CASCADE (knowledge_graph_*, promotions, versions).
            for tbl in ("brain.kg_edges", "brain.kg_nodes",
                        "brain.knowledge_graph_edges", "brain.knowledge_graph_nodes"):
                try:
                    cur.execute(
                        f"DELETE FROM {tbl} WHERE "
                        f"source_thought_id = %s OR target_thought_id = %s OR "
                        f"thought_id = %s",
                        (a, a, a),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
            try:
                cur.execute(
                    "DELETE FROM brain.thought_versions WHERE thought_id = %s",
                    (a,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                cur.execute(
                    "DELETE FROM brain.promotions WHERE thought_id = %s",
                    (a,),
                )
                conn.commit()
            except Exception:
                conn.rollback()

            cur.execute("DELETE FROM brain.thoughts WHERE thought_id = %s", (a,))
            conn.commit()

            # The cascade should have wiped the link.
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE link_id = %s",
                (link_id,),
            )
            assert cur.fetchone()[0] == 0, (
                "atom_links row should have cascaded on source delete"
            )
        finally:
            cur.close()


class TestTenantIsolation:
    def test_link_user_id_tenant_isolation(self, conn, test_user):
        # Write a link as user X; query as user Y returns no link.
        other_user = f"test-link-other-{uuid.uuid4().hex[:8]}"
        x_source = _insert_thought(conn, test_user, "X's source")
        x_target = _insert_thought(conn, test_user, "X's target")

        open_brain.add_link(
            conn,
            source_id=x_source,
            target_id=x_target,
            link_type="cites",
            user_id=test_user,
        )

        # As other_user — orphan-query, show-links, and unresolved query
        # must all return empty for the test_user's data.
        assert open_brain.query_orphan_links(conn, user_id=other_user) == []
        assert open_brain.query_unresolved_findings(conn, user_id=other_user) == []
        result = open_brain.show_links(conn, atom_id=x_source, user_id=other_user)
        assert result["outgoing"] == []
        assert result["incoming"] == []
        # And add_link as other_user trying to bind to X's source fails
        # the source-existence check (X's atom is not in other_user's scope).
        with pytest.raises(RuntimeError):
            open_brain.add_link(
                conn,
                source_id=x_source,
                target_id="gz-other-bead",
                link_type="references_bead",
                user_id=other_user,
            )

        # Cleanup other_user's rows (test_user fixture handles its own).
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM brain.atom_links WHERE user_id = %s",
                (other_user,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
