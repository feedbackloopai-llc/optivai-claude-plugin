#!/usr/bin/env python3
"""fblai-xhqfh — search replay metadata key-case bug tests.

Before the fix, search() normalized result dict keys to UPPERCASE (via
``{k.upper(): v for k, v in d.items()}``), but the replay-log emission
immediately below read ``results[0].get("thought_id")`` / ``.get("similarity")``
using LOWERCASE keys — always returning None.

After the fix the emit block reads ``results[0].get("THOUGHT_ID")`` and
``results[0].get("SIMILARITY")``.

Tests here:
  1. Unit-level: mock emit_replay_log and confirm the metadata dict passed
     to it has non-null top_thought_id and top_similarity when a result exists.
  2. Structural: confirm that the result dicts returned by search() carry
     THOUGHT_ID (uppercased) — so the emit-path fix was correct to use upper.

Tests skip cleanly without DATABASE_URL following the established pattern.

Run: python3 -m pytest scripts/tests/test_replay_search_meta.py -v
"""
import os
import sys
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

# Add scripts dir to path so we can import open_brain.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Module-level skip guard ─────────────────────────────────────────────────


def _get_conn_or_skip():
    """Return a live connection or skip the module if not available."""
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
        for tbl in ("brain.atom_links", "brain.replay_log", "brain.promotions"):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
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


@pytest.fixture()
def test_user(conn):
    """Unique test user id; cleans up its rows at teardown."""
    uid = f"test-replay-search-{uuid.uuid4().hex[:10]}"
    yield uid
    _cleanup_user(conn, uid)


# ─── Helper ──────────────────────────────────────────────────────────────────


def _capture_thought(conn, user_id: str, text: str) -> str:
    """Capture a thought (with real embedding) so search() can find it."""
    result = open_brain.capture(
        conn,
        text=text,
        user_id=user_id,
        source="test",
    )
    return result["thought_id"]


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestSearchReplayMetaKeyCaseFix:
    """After fblai-xhqfh fix: emit_replay_log receives non-null
    top_thought_id and top_similarity when search returns ≥1 result.
    """

    def test_result_dicts_carry_uppercased_thought_id_key(self, conn, test_user):
        """search() normalises result dict keys to UPPERCASE.

        Verify that returned dicts have 'THOUGHT_ID' (not 'thought_id') so the
        emit-block fix (reading 'THOUGHT_ID') is structurally correct.
        """
        _capture_thought(conn, test_user, "uppercase key test thought for replay")
        results = open_brain.search(conn, query="uppercase key test", user_id=test_user, limit=5)
        assert results, "Expected at least one result for 'uppercase key test' query."
        first = results[0]
        assert "THOUGHT_ID" in first, (
            f"Result dict is missing 'THOUGHT_ID' key. Present keys: {list(first.keys())}"
        )
        assert "thought_id" not in first, (
            "Result dict still has lowercase 'thought_id' key — normalisation broken."
        )

    def test_result_dicts_carry_uppercased_similarity_key(self, conn, test_user):
        """Companion to the THOUGHT_ID check: result dicts must have 'SIMILARITY'."""
        _capture_thought(conn, test_user, "similarity key test thought for replay check")
        results = open_brain.search(
            conn, query="similarity key test", user_id=test_user, limit=5
        )
        assert results, "Expected at least one result."
        first = results[0]
        assert "SIMILARITY" in first, (
            f"Result dict is missing 'SIMILARITY' key. Present keys: {list(first.keys())}"
        )

    def test_emit_replay_log_receives_nonnull_top_thought_id(self, conn, test_user):
        """When search() returns ≥1 result, the metadata dict passed to
        emit_replay_log must have a non-null 'top_thought_id'.

        We patch emit_replay_log (inside the open_brain module namespace)
        so the real DB write is bypassed; the spy captures the call args.
        """
        _capture_thought(conn, test_user, "replay meta nonnull thought_id test atom")
        captured_meta: list = []

        original_emit = open_brain.emit_replay_log

        def spy_emit(*args, **kwargs):
            meta = kwargs.get("metadata") or {}
            captured_meta.append(meta)
            # Still call the real function so the replay_log row lands.
            return original_emit(*args, **kwargs)

        with patch.object(open_brain, "emit_replay_log", side_effect=spy_emit):
            results = open_brain.search(
                conn,
                query="replay meta nonnull thought_id",
                user_id=test_user,
                limit=5,
            )

        assert results, "Expected ≥1 search result so top_thought_id can be non-null."

        # Find the search event metadata (there may be multiple calls from
        # memory-reinforcement UPDATE or Hebbian batch; the search replay is
        # the last call).
        search_metas = [m for m in captured_meta if "top_thought_id" in m]
        assert search_metas, (
            "emit_replay_log was never called with a 'top_thought_id' key in metadata."
        )
        last_meta = search_metas[-1]
        assert last_meta["top_thought_id"] is not None, (
            f"top_thought_id is None in search replay metadata. "
            f"This indicates the key-case bug is still present: "
            f"the emit block is still reading lowercase 'thought_id' from "
            f"the UPPERCASE-normalised result dict."
        )

    def test_emit_replay_log_receives_nonnull_top_similarity(self, conn, test_user):
        """Companion: the metadata passed to emit_replay_log must have a
        non-null 'top_similarity' when ≥1 result is returned.
        """
        _capture_thought(conn, test_user, "replay meta nonnull similarity test atom")
        captured_meta: list = []

        original_emit = open_brain.emit_replay_log

        def spy_emit(*args, **kwargs):
            meta = kwargs.get("metadata") or {}
            captured_meta.append(meta)
            return original_emit(*args, **kwargs)

        with patch.object(open_brain, "emit_replay_log", side_effect=spy_emit):
            results = open_brain.search(
                conn,
                query="replay meta nonnull similarity",
                user_id=test_user,
                limit=5,
            )

        assert results, "Expected ≥1 search result so top_similarity can be non-null."

        search_metas = [m for m in captured_meta if "top_similarity" in m]
        assert search_metas, (
            "emit_replay_log was never called with a 'top_similarity' key in metadata."
        )
        last_meta = search_metas[-1]
        assert last_meta["top_similarity"] is not None, (
            f"top_similarity is None in search replay metadata. "
            f"This indicates the key-case bug is still present: "
            f"the emit block is still reading lowercase 'similarity' from "
            f"the UPPERCASE-normalised result dict."
        )

    def test_zero_results_top_fields_are_none(self, conn, test_user):
        """When search returns 0 results, top_thought_id and top_similarity
        must be None (not raise an error).  This is the existing contract
        and must not be broken by the fix.
        """
        captured_meta: list = []
        original_emit = open_brain.emit_replay_log

        def spy_emit(*args, **kwargs):
            meta = kwargs.get("metadata") or {}
            captured_meta.append(meta)
            return original_emit(*args, **kwargs)

        with patch.object(open_brain, "emit_replay_log", side_effect=spy_emit):
            # Query a unique nonsense string that cannot match anything.
            results = open_brain.search(
                conn,
                query=f"zzz-nomatch-{uuid.uuid4().hex}",
                user_id=test_user,
                limit=1,
                threshold=0.9999,
            )

        assert results == [], "Expected 0 results for the nonsense query."
        search_metas = [m for m in captured_meta if "top_thought_id" in m]
        assert search_metas, "emit_replay_log was not called with search metadata."
        last_meta = search_metas[-1]
        assert last_meta["top_thought_id"] is None, (
            f"Expected top_thought_id=None for zero-result search, "
            f"got {last_meta['top_thought_id']!r}."
        )
        assert last_meta["top_similarity"] is None, (
            f"Expected top_similarity=None for zero-result search, "
            f"got {last_meta['top_similarity']!r}."
        )
