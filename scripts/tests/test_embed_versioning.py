#!/usr/bin/env python3
"""fblai-3yd1j — Embedding model versioning tests.

Verifies:
  (a) capture() writes embed_model + embed_dim into the INSERT params (mock cursor).
  (b) search() SQL includes the embed_model filter clause.
  (c) The dim-mismatch guard raises a clear ValueError when given a wrong-dim embedding.
  (d) The migration file exists, is idempotent (IF NOT EXISTS / WHERE IS NULL),
      and contains the correct backfill for 'all-mpnet-base-v2' / 768.

Tests (a)-(c) are offline — they mock DB connections and sentence-transformers.
Test (d) is static (file content check).

Run: python3 -m pytest scripts/tests/test_embed_versioning.py -v
"""
import inspect
import json
import os
import sys
import textwrap
import unittest.mock as mock
from pathlib import Path

import pytest

# Add scripts dir to sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import open_brain  # noqa: E402


# ─── (a) capture() writes embed_model + embed_dim ────────────────────────────

def _make_mock_conn_and_cur():
    """Return (mock_conn, mock_cur) with context-manager semantics."""
    mock_cur = mock.MagicMock()
    mock_conn = mock.MagicMock()
    mock_conn.cursor.return_value = mock_cur
    # Context manager support for `with conn.cursor() as cur:`
    mock_cur.__enter__ = mock.MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = mock.MagicMock(return_value=False)
    return mock_conn, mock_cur


def test_capture_writes_embed_model_and_dim():
    """capture() must include EMBED_MODEL and len(embedding) in the INSERT params."""
    mock_conn, mock_cur = _make_mock_conn_and_cur()
    mock_cur.fetchone.return_value = None  # no prior version row

    # A known 768-dim embedding so the dim-mismatch guard passes
    fake_embedding = [0.1] * 768

    with mock.patch("open_brain._connect", return_value=mock_conn), \
         mock.patch("open_brain._generate_embedding", return_value=fake_embedding), \
         mock.patch("open_brain._extract_metadata", return_value={
             "type": "decision",
             "summary": "test summary",
             "topics": [],
             "people": [],
             "action_items": [],
             "scope": "session",
             "confidence": "medium",
         }), \
         mock.patch("open_brain.emit_replay_log"), \
         mock.patch("open_brain._update_graph_incremental"):

        open_brain.capture(
            conn=mock_conn,
            text="test thought for embed versioning",
            user_id="testuser",
        )

    # Find the INSERT INTO brain.thoughts call
    insert_call = None
    for call in mock_cur.execute.call_args_list:
        args = call[0]
        if args and "INSERT INTO brain.thoughts" in str(args[0]):
            insert_call = call
            break

    assert insert_call is not None, (
        "Expected an INSERT INTO brain.thoughts execute() call; "
        f"calls were: {mock_cur.execute.call_args_list!r}"
    )

    # The second arg is the tuple of params
    params = insert_call[0][1]
    params_str = str(params)

    assert open_brain.EMBED_MODEL in params_str, (
        f"Expected EMBED_MODEL='{open_brain.EMBED_MODEL}' in INSERT params; "
        f"got params: {params!r}"
    )

    # embed_dim = len(fake_embedding) = 768 must be in params
    assert 768 in params, (
        f"Expected embed_dim=768 in INSERT params; got params: {params!r}"
    )


def test_capture_embed_model_is_correct_value():
    """The embed_model written to DB must be the module-level EMBED_MODEL constant."""
    mock_conn, mock_cur = _make_mock_conn_and_cur()
    mock_cur.fetchone.return_value = None

    fake_embedding = [0.1] * 768
    captured_params = []

    original_execute = mock_cur.execute

    def recording_execute(sql, params=None):
        if params is not None and "INSERT INTO brain.thoughts" in str(sql):
            captured_params.append(params)
        return original_execute(sql, params)

    mock_cur.execute = recording_execute

    with mock.patch("open_brain._connect", return_value=mock_conn), \
         mock.patch("open_brain._generate_embedding", return_value=fake_embedding), \
         mock.patch("open_brain._extract_metadata", return_value={
             "type": "insight",
             "summary": "s",
             "topics": [],
             "people": [],
             "action_items": [],
             "scope": "session",
             "confidence": "high",
         }), \
         mock.patch("open_brain.emit_replay_log"), \
         mock.patch("open_brain._update_graph_incremental"):

        open_brain.capture(
            conn=mock_conn,
            text="embed model test",
            user_id="testuser",
        )

    assert len(captured_params) >= 1, (
        "No INSERT INTO brain.thoughts execute call was recorded"
    )
    # embed_model should be exactly the EMBED_MODEL constant
    all_params_flat = [p for row in captured_params for p in row]
    assert open_brain.EMBED_MODEL in all_params_flat, (
        f"EMBED_MODEL='{open_brain.EMBED_MODEL}' not found in flat param list: {all_params_flat!r}"
    )


# ─── (b) search() SQL includes the embed_model filter ────────────────────────

def test_search_sql_includes_embed_model_filter():
    """search() must include 'embed_model = %s OR embed_model IS NULL' in the WHERE clause."""
    mock_conn, mock_cur = _make_mock_conn_and_cur()
    mock_cur.fetchall.return_value = []
    mock_cur.description = []

    # Capture every SQL+params pair passed to execute
    executed_sqls = []
    executed_params = []

    def recording_execute(sql, params=None):
        executed_sqls.append(str(sql))
        executed_params.append(params)

    mock_cur.execute = recording_execute

    fake_embedding = [0.1] * 768

    with mock.patch("open_brain._generate_embedding", return_value=fake_embedding), \
         mock.patch("open_brain.compute_effective_weights_batch", return_value={}), \
         mock.patch("open_brain.emit_replay_log"):

        open_brain.search(
            conn=mock_conn,
            query="test query",
            user_id="testuser",
        )

    # Find the main search SQL
    search_sql_calls = [
        (sql, params)
        for sql, params in zip(executed_sqls, executed_params)
        if "vec_similarity" in sql or "hybrid_score" in sql
    ]

    assert len(search_sql_calls) >= 1, (
        f"No search SQL found in execute calls: {executed_sqls!r}"
    )

    search_sql, search_params = search_sql_calls[0]

    assert "embed_model" in search_sql.lower(), (
        "Expected 'embed_model' filter in search SQL WHERE clause; "
        f"search SQL:\n{search_sql}"
    )

    assert "embed_model IS NULL" in search_sql.lower() or "is null" in search_sql.lower(), (
        "Expected 'embed_model IS NULL' defensive tolerance in search WHERE; "
        f"search SQL:\n{search_sql}"
    )

    # The EMBED_MODEL value must be in the params tuple
    params_flat = list(search_params) if search_params else []
    assert open_brain.EMBED_MODEL in params_flat, (
        f"Expected EMBED_MODEL='{open_brain.EMBED_MODEL}' in search params; "
        f"got: {params_flat!r}"
    )


# ─── (c) dim-mismatch guard raises clear error ───────────────────────────────

def test_dim_mismatch_guard_raises_value_error():
    """capture() must raise a clear ValueError when embedding dim != 768."""
    mock_conn, mock_cur = _make_mock_conn_and_cur()
    mock_cur.fetchone.return_value = None

    # Return a 384-dim embedding (wrong dim for all-mpnet-base-v2 schema)
    wrong_dim_embedding = [0.1] * 384

    with mock.patch("open_brain._connect", return_value=mock_conn), \
         mock.patch("open_brain._generate_embedding", return_value=wrong_dim_embedding), \
         mock.patch("open_brain._extract_metadata", return_value={
             "type": "insight",
             "summary": "s",
             "topics": [],
             "people": [],
             "action_items": [],
             "scope": "session",
             "confidence": "medium",
         }):

        with pytest.raises(ValueError) as exc_info:
            open_brain.capture(
                conn=mock_conn,
                text="test dim mismatch",
                user_id="testuser",
            )

    error_msg = str(exc_info.value)

    # Must name the offending dim
    assert "384" in error_msg, (
        f"Error message should include the actual dim (384); got: {error_msg!r}"
    )

    # Must name the expected dim
    assert "768" in error_msg, (
        f"Error message should include the expected dim (768); got: {error_msg!r}"
    )

    # Must mention schema migration
    assert "schema migration" in error_msg.lower() or "migration" in error_msg.lower(), (
        f"Error message should mention schema migration; got: {error_msg!r}"
    )

    # Confirm no DB INSERT happened
    for call in mock_cur.execute.call_args_list:
        args = call[0]
        if args and "INSERT INTO brain.thoughts" in str(args[0]):
            pytest.fail(
                "DB INSERT must NOT be called when dim-mismatch guard fires; "
                f"INSERT was called with: {args!r}"
            )


def test_dim_mismatch_guard_does_not_fire_for_correct_dim():
    """capture() must NOT raise when embedding dim == 768 (normal path)."""
    mock_conn, mock_cur = _make_mock_conn_and_cur()
    mock_cur.fetchone.return_value = None

    correct_embedding = [0.1] * 768

    with mock.patch("open_brain._connect", return_value=mock_conn), \
         mock.patch("open_brain._generate_embedding", return_value=correct_embedding), \
         mock.patch("open_brain._extract_metadata", return_value={
             "type": "insight",
             "summary": "s",
             "topics": [],
             "people": [],
             "action_items": [],
             "scope": "session",
             "confidence": "medium",
         }), \
         mock.patch("open_brain.emit_replay_log"), \
         mock.patch("open_brain._update_graph_incremental"):

        # Must NOT raise
        try:
            open_brain.capture(
                conn=mock_conn,
                text="correct dim test",
                user_id="testuser",
            )
        except ValueError as e:
            if "dimension mismatch" in str(e).lower():
                pytest.fail(f"Dim-mismatch guard raised for correct 768-dim embedding: {e}")


# ─── (d) Migration file is correct and idempotent ────────────────────────────

_MIGRATION_FILE = (
    _REPO_ROOT / "sql" / "migrations" / "2026-06-11-embed-versioning.sql"
)


def test_embed_versioning_migration_file_exists():
    """Migration file 2026-06-11-embed-versioning.sql must exist."""
    assert _MIGRATION_FILE.exists(), (
        f"Migration file not found: {_MIGRATION_FILE}"
    )


def test_embed_versioning_migration_is_idempotent():
    """Migration must use ADD COLUMN IF NOT EXISTS and WHERE embed_model IS NULL."""
    assert _MIGRATION_FILE.exists(), f"Migration file not found: {_MIGRATION_FILE}"
    sql = _MIGRATION_FILE.read_text(encoding="utf-8").upper()

    assert "ADD COLUMN IF NOT EXISTS" in sql, (
        "Migration must use ADD COLUMN IF NOT EXISTS for idempotency; "
        f"got:\n{sql[:800]}"
    )

    assert "WHERE EMBED_MODEL IS NULL" in sql, (
        "Migration backfill must guard with WHERE embed_model IS NULL; "
        f"got:\n{sql[:800]}"
    )


def test_embed_versioning_migration_backfills_correct_model():
    """Migration backfill must use 'all-mpnet-base-v2' and 768."""
    assert _MIGRATION_FILE.exists(), f"Migration file not found: {_MIGRATION_FILE}"
    sql = _MIGRATION_FILE.read_text(encoding="utf-8")

    assert "all-mpnet-base-v2" in sql, (
        "Migration backfill must set embed_model = 'all-mpnet-base-v2'; "
        f"got:\n{sql[:800]}"
    )

    assert "768" in sql, (
        "Migration backfill must set embed_dim = 768; "
        f"got:\n{sql[:800]}"
    )

    # Also check thought_versions is updated
    assert "thought_versions" in sql, (
        "Migration must also update brain.thought_versions; "
        f"got:\n{sql[:800]}"
    )


# ─── (e) graph_search expansion fetch carries the embed_model filter ─────────
#
# Review FIX 2 (fblai-3yd1j): graph_search()'s step-3 batch fetch of
# graph-expanded atoms must apply the same embed_model filter as search(),
# else a graph hop can surface a stale-model atom under a model migration.

class _SqlDrivenCursor:
    """A mock cursor that returns canned results based on SQL content.

    Drives graph_search() far enough to reach the step-3 fetch.  Records
    every (sql, params) pair so the test can inspect the fetch.
    """

    def __init__(self, neighbor_thought_id: str, user_id: str):
        self.neighbor_thought_id = neighbor_thought_id
        self.user_id = user_id
        self.calls = []  # list of (sql, params)
        self._last_rows = []
        self._last_one = None
        self.description = [
            ("thought_id",), ("raw_text",), ("summary",), ("thought_type",),
            ("topics",), ("people",), ("action_items",), ("source",),
            ("project",), ("created_at",), ("stv_frequency",), ("stv_confidence",),
        ]

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        s = str(sql)
        # node_count probe
        if "COUNT(*)" in s and "knowledge_graph_nodes" in s:
            self._last_one = (1,)  # node_count > 0
            self._last_rows = []
        # seed node lookup
        elif "node_id, node_nk" in s and "knowledge_graph_nodes" in s and "node_nk IN" in s:
            self._last_rows = [(101, "thought:seed-1")]
            self._last_one = None
        # kg_neighborhood expansion → returns the new neighbor thought
        elif "kg_neighborhood" in s:
            # (node_id, node_nk, node_type, name, min_depth)
            self._last_rows = [
                (202, f"thought:{self.neighbor_thought_id}", "thought", "neighbor", 1),
            ]
            self._last_one = None
        # atom_links 1-hop expansion → nothing
        elif "atom_links" in s:
            self._last_rows = []
            self._last_one = None
        # step-3 fetch — return one row for the neighbor (12 columns)
        elif "FROM brain.thoughts" in s and "thought_id IN" in s:
            self._last_rows = [(
                self.neighbor_thought_id, "raw", "summary", "insight",
                "[]", "[]", "[]", "test", "", "2026-06-01 00:00:00", 1.0, 0.7,
            )]
            self._last_one = None
        else:
            self._last_rows = []
            self._last_one = None

    def fetchone(self):
        return self._last_one

    def fetchall(self):
        return self._last_rows

    def close(self):
        pass


def test_graph_search_fetch_includes_embed_model_filter():
    """graph_search step-3 fetch SQL must carry the embed_model filter + EMBED_MODEL param."""
    neighbor_id = "graph-neighbor-1"
    user_id = "testuser"

    sql_cursor = _SqlDrivenCursor(neighbor_id, user_id)
    mock_conn = mock.MagicMock()
    mock_conn.cursor.return_value = sql_cursor

    # Seed result from search() — a single seed atom.
    seed_result = {
        "THOUGHT_ID": "seed-1",
        "HYBRID_SCORE": 0.9,
        "SIMILARITY": 0.9,
        "THOUGHT_TYPE": "insight",
        "SUMMARY": "seed",
        "RAW_TEXT": "seed text",
        "CREATED_AT": "2026-06-01 00:00:00",
        "TOPICS": [],
        "PEOPLE": [],
        "ACTION_ITEMS": [],
        "KEYWORD_BOOST": 0.0,
        "TIME_DECAY": 0.0,
        "STV": {"f": 1.0, "c": 0.7},
    }

    with mock.patch("open_brain.search", return_value=[seed_result]), \
         mock.patch("open_brain.emit_replay_log"):
        open_brain.graph_search(
            conn=mock_conn,
            query="test query",
            user_id=user_id,
        )

    # Locate the step-3 fetch call.
    fetch_calls = [
        (sql, params)
        for sql, params in sql_cursor.calls
        if "FROM brain.thoughts" in sql and "thought_id IN" in sql
    ]

    assert len(fetch_calls) >= 1, (
        "graph_search did not reach the step-3 fetch; "
        f"recorded SQL calls: {[c[0][:80] for c in sql_cursor.calls]!r}"
    )

    fetch_sql, fetch_params = fetch_calls[0]

    assert "embed_model" in fetch_sql.lower(), (
        "graph_search step-3 fetch must include the embed_model filter; "
        f"fetch SQL:\n{fetch_sql}"
    )

    assert "embed_model is null" in fetch_sql.lower(), (
        "graph_search step-3 fetch must include the 'embed_model IS NULL' "
        f"defensive tolerance (mirroring search()); fetch SQL:\n{fetch_sql}"
    )

    # EMBED_MODEL must be present in the params tuple.
    params_flat = list(fetch_params) if fetch_params else []
    assert open_brain.EMBED_MODEL in params_flat, (
        f"Expected EMBED_MODEL='{open_brain.EMBED_MODEL}' in graph_search fetch params; "
        f"got: {params_flat!r}"
    )

    # The user_id must immediately precede EMBED_MODEL in the params (clause order),
    # confirming the param alignment matches the SQL (... user_id = %s AND
    # (embed_model = %s OR ...)).
    uid_idx = params_flat.index(user_id)
    assert params_flat[uid_idx + 1] == open_brain.EMBED_MODEL, (
        "EMBED_MODEL param must immediately follow user_id to match the WHERE "
        f"clause order; got params: {params_flat!r}"
    )
