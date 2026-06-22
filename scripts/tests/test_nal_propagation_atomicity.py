#!/usr/bin/env python3
"""Tests for NAL evidence-propagation snapshot+update atomicity (fblai-fzwlw).

The fix uses orphan-cleanup: snapshot_thought commits internally (its
FOR UPDATE lock + conn.commit() + emit_replay_log commit cannot be cleanly
deferred without restructuring ~15 callers). If the subsequent stv UPDATE
fails, the newly-created version row is DELETE'd so the audit trail does not
claim an nal_evidence revision that never applied to the live row.

Tests:
(a) No orphan version row when UPDATE is forced to fail.
(b) Version row IS present when UPDATE succeeds (regression guard).
(c) Cleanup failure path logs a warning but add_link does not raise.

Run:
    python3 -m pytest scripts/tests/test_nal_propagation_atomicity.py -v
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Connection wrapper (psycopg2 cursor is a C-level read-only attribute) ───
#
# `patch.object(conn, "cursor", ...)` raises AttributeError because
# psycopg2.extensions.connection.cursor is a C-level slot — read-only on the
# instance.  We wrap the real connection in a pure-Python class so we can
# substitute cursor() freely without touching the C layer.


class _ConnWrapper:
    """Pure-Python wrapper around a psycopg2 connection.

    Delegates everything to the underlying connection, but ``cursor``
    is a regular Python method that can be replaced by tests.
    """

    def __init__(self, real_conn):
        self._real = real_conn
        # Reference to the real cursor factory — tests replace this.
        self._cursor_factory = real_conn.cursor

    def cursor(self):
        return self._cursor_factory()

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()

    # psycopg2 extras sometimes read these attributes.
    @property
    def closed(self):
        return self._real.closed

    @property
    def status(self):
        return self._real.status

    @property
    def encoding(self):
        return self._real.encoding


# ─── DB fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_conn():
    """Module-scoped live Postgres connection (two-connection pattern)."""
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
    c_init = open_brain._connect()
    open_brain.init_schema(c_init)
    c_init.close()
    c = open_brain._connect()
    yield c
    c.close()


@pytest.fixture()
def conn(real_conn):
    """Return a _ConnWrapper around the module-scoped real connection."""
    return _ConnWrapper(real_conn)


@pytest.fixture()
def test_user(conn):
    """Per-test isolated user; cleaned up on teardown."""
    uid = f"test-atom-{uuid.uuid4().hex[:12]}"
    yield uid
    # Cleanup uses the wrapped conn (delegates to the real conn).
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
) -> str:
    """Insert a minimal thought row directly for test setup (bypasses LLM)."""
    thought_id = open_brain._generate_thought_id()
    prov_agent = open_brain._derive_prov_agent("test", user_id)
    was_generated_by = open_brain._generate_activity_id(thought_id)
    embedding = open_brain._generate_embedding(text)
    metadata: Dict[str, Any] = {
        "type": "insight", "topics": [], "people": [],
        "action_items": [], "summary": text[:200],
    }
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


def _count_versions(conn, thought_id: str) -> int:
    """Return the number of thought_versions rows for thought_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM brain.thought_versions WHERE thought_id = %s",
            (thought_id,),
        )
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _get_live_stv(conn, thought_id: str):
    """Return (stv_frequency, stv_confidence) for the live row."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT stv_frequency, stv_confidence FROM brain.thoughts "
            "WHERE thought_id = %s",
            (thought_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None, None
        return float(row[0]), float(row[1])
    finally:
        cur.close()


# ─── (a) No orphan version row when UPDATE is forced to fail ─────────────────


class TestOrphanCleanupOnUpdateFailure:
    """When the stv UPDATE fails after the snapshot committed, the orphan
    version row must be cleaned up so the audit trail stays honest."""

    @pytest.mark.integration
    def test_no_orphan_version_row_when_update_fails(self, conn, test_user):
        """Force the stv UPDATE to raise after snapshot commits; assert
        no nal_evidence version row remains for the target.

        Strategy: replace conn._cursor_factory with a factory that returns
        a cursor wrapper which raises on the specific stv UPDATE SQL.
        The _ConnWrapper fixture exposes _cursor_factory as a regular Python
        attribute that can be replaced freely.
        """
        source = _plant_thought(conn, test_user, text="atomicity test source",
                                stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user, text="atomicity test target",
                                stv_f=1.0, stv_c=0.5)

        pre_version_count = _count_versions(conn, target)
        pre_f, pre_c = _get_live_stv(conn, target)

        real_factory = conn._cursor_factory

        class FailOnUpdateCursor:
            """Thin wrapper: raises only on the stv UPDATE SQL."""

            def __init__(self):
                self._real = real_factory()

            def execute(self, sql, params=None):
                if (
                    "UPDATE brain.thoughts SET stv_frequency" in sql
                    and "stv_confidence" in sql
                    and "updated_at = NOW()" in sql
                ):
                    raise RuntimeError("forced UPDATE failure for atomicity test")
                return self._real.execute(sql, params)

            def fetchone(self):
                return self._real.fetchone()

            def fetchall(self):
                return self._real.fetchall()

            @property
            def rowcount(self):
                return self._real.rowcount

            def close(self):
                return self._real.close()

        conn._cursor_factory = FailOnUpdateCursor
        try:
            # add_link's evidence propagation is best-effort — it catches
            # the RuntimeError and logs it. No exception should propagate.
            result = open_brain.add_link(
                conn,
                source_id=source,
                target_id=target,
                link_type="verifies",
                user_id=test_user,
            )
        finally:
            conn._cursor_factory = real_factory  # always restore

        # The link itself should have been committed (best-effort contract).
        assert result.get("created") is True, (
            "Link should still be created despite propagation failure"
        )

        # KEY ASSERTION: no orphan nal_evidence version row should remain.
        post_version_count = _count_versions(conn, target)
        assert post_version_count == pre_version_count, (
            f"Orphan version row was left after UPDATE failure: "
            f"pre={pre_version_count}, post={post_version_count}. "
            f"The orphan-cleanup path did not run or failed silently."
        )

        # The live stv should be UNCHANGED (UPDATE failed and rolled back).
        post_f, post_c = _get_live_stv(conn, target)
        assert abs(post_f - pre_f) < 1e-6, (
            f"Live stv_frequency should be unchanged after failed UPDATE: "
            f"{pre_f} → {post_f}"
        )
        assert abs(post_c - pre_c) < 1e-6, (
            f"Live stv_confidence should be unchanged after failed UPDATE: "
            f"{pre_c} → {post_c}"
        )


# ─── (b) Version row present when UPDATE succeeds ────────────────────────────


class TestVersionRowPresentOnSuccess:
    """Regression guard: the happy path still produces a version row."""

    @pytest.mark.integration
    def test_version_row_created_on_success(self, conn, test_user):
        """A successful verifies link creates exactly one nal_evidence version
        row for the target and updates the live stv.
        """
        source = _plant_thought(conn, test_user, text="success-path source",
                                stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user, text="success-path target",
                                stv_f=1.0, stv_c=0.5)

        pre_count = _count_versions(conn, target)
        pre_f, pre_c = _get_live_stv(conn, target)

        open_brain.add_link(
            conn,
            source_id=source,
            target_id=target,
            link_type="verifies",
            user_id=test_user,
        )

        post_count = _count_versions(conn, target)
        post_f, post_c = _get_live_stv(conn, target)

        # One version row should have been added.
        assert post_count == pre_count + 1, (
            f"Expected exactly one new version row; "
            f"pre={pre_count}, post={post_count}"
        )

        # Verify the version row has prov_activity='nal_evidence'.
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT prov_activity FROM brain.thought_versions
                WHERE thought_id = %s ORDER BY revision DESC LIMIT 1
                """,
                (target,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
        assert row is not None
        assert row[0] == "nal_evidence", (
            f"Expected prov_activity='nal_evidence', got {row[0]!r}"
        )

        # Live stv should have changed (evidence was applied).
        assert post_c > pre_c, (
            f"Live stv_confidence should increase: {pre_c} → {post_c}"
        )


# ─── (c) Cleanup failure logs warning but add_link does not raise ─────────────


class TestOrphanCleanupFailureHandling:
    """If the orphan DELETE itself fails, the code must log a warning but
    NOT raise — evidence propagation is best-effort and must never block
    the link addition.
    """

    @pytest.mark.integration
    def test_cleanup_failure_logs_warning(self, conn, test_user, caplog):
        """Force both UPDATE and cleanup DELETE to fail; assert a warning
        is logged containing 'orphan-cleanup' and add_link still succeeds.
        """
        source = _plant_thought(conn, test_user,
                                text="cleanup-fail source", stv_f=1.0, stv_c=0.8)
        target = _plant_thought(conn, test_user,
                                text="cleanup-fail target", stv_f=1.0, stv_c=0.5)

        real_factory = conn._cursor_factory

        class FailBothCursor:
            """Fails on the stv UPDATE AND on the cleanup DELETE."""

            def __init__(self):
                self._real = real_factory()

            def execute(self, sql, params=None):
                is_stv_update = (
                    "UPDATE brain.thoughts SET stv_frequency" in sql
                    and "updated_at = NOW()" in sql
                )
                is_cleanup_delete = (
                    "DELETE FROM brain.thought_versions" in sql
                    and "version_id" in sql
                )
                if is_stv_update:
                    raise RuntimeError("forced UPDATE failure")
                if is_cleanup_delete:
                    raise RuntimeError("forced DELETE failure")
                return self._real.execute(sql, params)

            def fetchone(self):
                return self._real.fetchone()

            def fetchall(self):
                return self._real.fetchall()

            @property
            def rowcount(self):
                return self._real.rowcount

            def close(self):
                return self._real.close()

        conn._cursor_factory = FailBothCursor
        try:
            with caplog.at_level(logging.WARNING, logger="open_brain"):
                result = open_brain.add_link(
                    conn,
                    source_id=source,
                    target_id=target,
                    link_type="verifies",
                    user_id=test_user,
                )
        finally:
            conn._cursor_factory = real_factory  # always restore

        # add_link must still succeed (best-effort propagation).
        assert "link_id" in result or "created" in result, (
            f"add_link should succeed despite propagation failure: {result}"
        )

        # A warning must have been logged about the cleanup failure.
        cleanup_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "orphan-cleanup" in r.message.lower()
        ]
        assert cleanup_warnings, (
            "Expected a WARNING log about orphan-cleanup failure. "
            f"Logged records: {[(r.levelno, r.message) for r in caplog.records]}"
        )
