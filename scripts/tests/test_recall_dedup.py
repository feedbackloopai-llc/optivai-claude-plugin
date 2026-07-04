#!/usr/bin/env python3
"""T3 / fblai-bfyjr — semantic dedup in open_brain.py search() + the
--inspect live-row fallback (CCR-reversibility gap, R1).

Concrete-contract tests against the already-gated T1 design at
``docs/plans/2026-07-02-recall-assembly-t1-design.md`` §3 (dedup),
§4.3/§4.4 (the --inspect live-row fallback + R1/R3), §5 (F5), §6.2 (the
T3 gate).

Two halves:

  (a) DEDUP — pure unit tests on ``open_brain._semantic_dedup`` (no DB):
      D1-D9 (§3.8), R3 (§4.4), F5 (§5). Plus mocked-connection tests (no
      DB) proving D7 byte-stability and the dedup=True over-fetch/
      embedding-select behavior at the SQL level, including a real
      byte-identical golden-SQL comparison (fblai-6zu4p,
      TestD7GoldenSqlByteStability).

  (b) INSPECT LIVE-ROW FALLBACK (security-sensitive) — unit tests on
      ``time_travel.inspect_live`` with a MOCKED connection (no DB) that
      prove it reuses ``_assert_in_scope`` (the identical user-scoping
      predicate) and that a wrong-principal RuntimeError propagates
      (the fallback refuses, never reads cross-scope). Also covers the
      mirrored fallback in the Pi ``op:inspect`` dispatch
      (``open_brain._run_from_pi``, fblai-egxj9) via
      ``TestOpInspectPiFallback``, driving the real dispatch function
      end to end with mocked stdin/_connect/_get_user_id.

Integration tests (R1 round-trip, D9 reinforcement-touch-scope) require a
live Postgres (DATABASE_URL) and are SKIPPED cleanly when absent, mirroring
the established pattern in scripts/tests/test_atom_links.py and
scripts/tests/test_replay_search_meta.py.

Run:
    python3 -m pytest scripts/tests/test_recall_dedup.py -v
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402
import time_travel  # noqa: E402


# ─── Fixture helpers (pure; no DB) ────────────────────────────────────────────


def _id(i: int) -> str:
    """A realistic-looking brain-{epoch}-{8hex} thought id (matches R3's regex)."""
    return f"brain-{1720000000 + i}-{i:08x}"


def _atom(tid: str, embedding=None, hybrid_score: float = 0.5,
          stv=None, created_at: str = "2026-07-01T12:00:00", **extra) -> dict:
    """A search()-shaped result dict (post-uppercase, pre-dedup)."""
    atom = {
        "THOUGHT_ID": tid,
        "HYBRID_SCORE": hybrid_score,
        "CREATED_AT": created_at,
        "STV": dict(stv) if stv is not None else {"f": 1.0, "c": 0.5},
    }
    if embedding is not None:
        atom["_EMBEDDING"] = list(embedding)
    atom.update(extra)
    return atom


def _is_subsequence(sub: list, full: list) -> bool:
    it = iter(full)
    return all(elem in it for elem in sub)


BRAIN_ID_RE = re.compile(r"^brain-\d+-[a-f0-9]{8}$")


# ─── (a) DEDUP — D1-D9, R3, F5 (pure unit tests on _semantic_dedup) ──────────


class TestSemanticDedupD1toD9:
    """Maps onto the T1 design §3.8 invariants D1-D9."""

    def test_d1_threshold_collapse_three_near_duplicates(self):
        v = [1.0, 0.0, 0.0, 0.0]
        v_near1 = [0.999, 0.0447, 0.0, 0.0]   # cosine ~0.999 vs v
        v_near2 = [0.995, 0.0999, 0.0, 0.0]   # cosine ~0.995 vs v
        atoms = [
            _atom(_id(1), embedding=v, hybrid_score=0.90),
            _atom(_id(2), embedding=v_near1, hybrid_score=0.85),
            _atom(_id(3), embedding=v_near2, hybrid_score=0.80),
        ]
        survivors = open_brain._semantic_dedup(atoms)
        assert len(survivors) == 1
        assert survivors[0]["THOUGHT_ID"] == _id(1)
        assert survivors[0]["NEAR_DUPLICATE_COUNT"] == 2
        assert survivors[0]["NEAR_DUPLICATE_IDS"] == [_id(2), _id(3)]

    def test_d2_missing_embedding_fail_open_never_absorbs_or_absorbed(self):
        v = [1.0, 0.0, 0.0, 0.0]
        atoms = [
            _atom(_id(1), embedding=v),
            _atom(_id(2)),                    # no _EMBEDDING key at all
            _atom(_id(3), embedding=v),       # identical to atom1 -> collapses
        ]
        survivors = open_brain._semantic_dedup(atoms)  # must not raise
        ids = [s["THOUGHT_ID"] for s in survivors]
        assert _id(1) in ids
        assert _id(2) in ids, "embedding-less row must always survive (D2 fail-open)"
        assert _id(3) not in ids
        s1 = next(s for s in survivors if s["THOUGHT_ID"] == _id(1))
        assert s1["NEAR_DUPLICATE_IDS"] == [_id(3)]
        s2 = next(s for s in survivors if s["THOUGHT_ID"] == _id(2))
        assert s2.get("NEAR_DUPLICATE_COUNT", 0) == 0, "embedding-less row never absorbs"

    def test_d3_survivor_order_is_subsequence_of_pre_dedup_order(self):
        v = [1.0, 0.0, 0.0, 0.0]
        w = [0.0, 1.0, 0.0, 0.0]
        atoms = [
            _atom(_id(1), embedding=v),
            _atom(_id(2), embedding=w),
            _atom(_id(3), embedding=v),   # collapses into 1
            _atom(_id(4), embedding=w),   # collapses into 2
            _atom(_id(5), embedding=v),   # collapses into 1
        ]
        input_order = [a["THOUGHT_ID"] for a in atoms]
        survivors = open_brain._semantic_dedup(atoms)
        survivor_order = [s["THOUGHT_ID"] for s in survivors]
        assert survivor_order == [_id(1), _id(2)]
        assert _is_subsequence(survivor_order, input_order)

    def test_d4_no_resurrection_every_output_id_was_in_input(self):
        v = [1.0, 0.0, 0.0, 0.0]
        atoms = [_atom(_id(i), embedding=v) for i in range(1, 6)]
        input_ids = {a["THOUGHT_ID"] for a in atoms}
        survivors = open_brain._semantic_dedup(atoms)
        output_ids = set()
        for s in survivors:
            output_ids.add(s["THOUGHT_ID"])
            output_ids.update(s.get("NEAR_DUPLICATE_IDS", []))
        assert output_ids == input_ids, "no id fabricated, no id silently dropped"

    def test_d5_idempotence_dedup_of_dedup_equals_dedup(self):
        v = [1.0, 0.0, 0.0, 0.0]
        w = [0.0, 1.0, 0.0, 0.0]
        atoms = [
            _atom(_id(1), embedding=v),
            _atom(_id(2), embedding=v),
            _atom(_id(3), embedding=w),
        ]
        once = open_brain._semantic_dedup(copy.deepcopy(atoms))
        twice = open_brain._semantic_dedup(
            open_brain._semantic_dedup(copy.deepcopy(atoms))
        )
        assert once == twice

    def test_d6_no_embedding_key_egresses_from_pure_helper(self):
        v = [1.0, 0.0, 0.0, 0.0]
        atoms = [_atom(_id(1), embedding=v), _atom(_id(2), embedding=v)]
        survivors = open_brain._semantic_dedup(atoms)
        for s in survivors:
            assert "_EMBEDDING" not in s

    def test_d8_distinct_atoms_below_threshold_all_survive_counts_absent(self):
        v1 = [1.0, 0.0, 0.0, 0.0]
        v2 = [0.4, 0.9165151, 0.0, 0.0]  # cosine(v1, v2) ~= 0.4
        cos = open_brain._cosine_similarity(v1, v2)
        assert 0.35 < cos < 0.45, "fixture sanity check: cosine should be ~0.4"
        atoms = [_atom(_id(1), embedding=v1), _atom(_id(2), embedding=v2)]
        survivors = open_brain._semantic_dedup(atoms)
        assert len(survivors) == 2
        for s in survivors:
            assert "NEAR_DUPLICATE_COUNT" not in s
            assert "NEAR_DUPLICATE_IDS" not in s

    def test_d9_unit_survivors_only_output_no_collapsed_ids(self):
        """Structural half of D9 (reinforcement scope): the object that
        search() hands to the reinforcement-touch block is exactly
        _semantic_dedup()'s (truncated) return value, which by
        construction excludes collapsed atoms. See
        TestD9ReinforcementScopeIntegration for the DB-backed proof that
        updated_at is actually left untouched for collapsed atoms.
        """
        v = [1.0, 0.0, 0.0, 0.0]
        atoms = [_atom(_id(1), embedding=v), _atom(_id(2), embedding=v)]
        survivors = open_brain._semantic_dedup(atoms)
        survivor_ids = {s["THOUGHT_ID"] for s in survivors}
        assert survivor_ids == {_id(1)}
        assert _id(2) not in survivor_ids


class TestR3AndF5:
    """R3 (§4.4): NEAR_DUPLICATE_IDS are full ids. F5 (§5): STV untouched."""

    def test_r3_near_duplicate_ids_are_full_ids_not_short_ids(self):
        v = [1.0, 0.0, 0.0, 0.0]
        atoms = [_atom(_id(i), embedding=v) for i in range(1, 4)]
        survivors = open_brain._semantic_dedup(atoms)
        assert len(survivors) == 1
        dup_ids = survivors[0]["NEAR_DUPLICATE_IDS"]
        assert len(dup_ids) == 2
        for dup_id in dup_ids:
            assert BRAIN_ID_RE.match(dup_id), f"{dup_id!r} is not a full brain-* id"

    def test_f5_stv_bit_identical_pre_post_dedup(self):
        v = [1.0, 0.0, 0.0, 0.0]
        stv_a = {"f": 0.87, "c": 0.63}
        stv_b = {"f": 0.42, "c": 0.91}
        atoms = [
            _atom(_id(1), embedding=v, stv=stv_a),
            _atom(_id(2), embedding=v, stv=stv_b),  # collapses into atom1
        ]
        survivors = open_brain._semantic_dedup(atoms)
        assert len(survivors) == 1
        assert survivors[0]["STV"] == stv_a, (
            "survivor STV must be bit-identical to its pre-dedup value — "
            "dedup never computes, averages, or fabricates stv (F5)"
        )


# ─── D7 + over-fetch — mocked conn/cursor, no DB required ────────────────────


class TestD7SqlByteStability:
    """D7: search(..., dedup=False) — the default — executes the exact same
    SQL shape as pre-T3 (no embedding column, no over-fetch). Complementary
    check: dedup=True DOES select the embedding and DOES over-fetch.
    """

    @staticmethod
    def _make_mock_conn():
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.description = []
        mock_cur.fetchall.return_value = []
        return mock_conn, mock_cur

    def test_dedup_false_sql_has_no_embedding_column_and_no_overfetch(self):
        mock_conn, mock_cur = self._make_mock_conn()
        with patch.object(open_brain, "_generate_embedding", return_value=[0.1] * 768), \
             patch.object(open_brain, "emit_replay_log", return_value=1):
            open_brain.search(
                mock_conn, query="byte stability test", user_id="user-x",
                limit=5, dedup=False,
            )

        assert mock_cur.execute.call_count == 1
        sql_text, params = mock_cur.execute.call_args_list[0][0]
        assert "_embedding_text" not in sql_text
        assert "embedding::text" not in sql_text
        assert params[-1] == 5, "LIMIT must be the caller's limit, unmodified"

    def test_dedup_true_sql_selects_embedding_and_overfetches(self):
        mock_conn, mock_cur = self._make_mock_conn()
        with patch.object(open_brain, "_generate_embedding", return_value=[0.1] * 768), \
             patch.object(open_brain, "emit_replay_log", return_value=1):
            open_brain.search(
                mock_conn, query="overfetch test", user_id="user-x",
                limit=5, dedup=True,
            )

        sql_text, params = mock_cur.execute.call_args_list[0][0]
        assert "embedding::text" in sql_text
        assert "_embedding_text" in sql_text
        expected_limit = min(5 * open_brain.DEDUP_OVERFETCH_FACTOR, open_brain.DEDUP_OVERFETCH_CAP)
        assert params[-1] == expected_limit


class TestD7GoldenSqlByteStability:
    """D7 golden-SQL byte-stability (fblai-6zu4p): a REAL ``==`` byte
    comparison of the dedup=False ``search_sql`` string against a
    hardcoded golden constant - not a substring/whitespace-loose match
    like ``TestD7SqlByteStability`` above. This is the pre-T3 baseline:
    if anyone drifts the dedup=False SQL template (adds a column, changes
    whitespace, reorders a clause), this test REDS. That is the point -
    it is the durable backstop for the D7 "degrade-to-today" invariant.

    Query is deliberately all-stopword ("is a to" - every token is in
    STOP_WORDS) so ``_extract_keywords`` returns ``[]`` and
    ``keyword_boost_expr`` collapses to the constant ``"0.0"`` branch -
    this keeps the golden SQL free of query-dependent CASE clauses and
    fully deterministic. ``_generate_embedding`` is patched to a fixed
    768-dim vector for the same reason (the embedding value flows into
    ``params``, not into ``search_sql`` itself, but pinning it removes
    any doubt).
    """

    _QUERY = "is a to"  # all-stopword: _extract_keywords(...) == []

    # Captured verbatim (via repr()) from the current dedup=False
    # search_sql f-string output in open_brain.search() - see docstring.
    _GOLDEN_DEDUP_FALSE_SQL = '\n        WITH scored AS (\n            SELECT\n                thought_id,\n                raw_text,\n                summary,\n                thought_type,\n                topics,\n                people,\n                action_items,\n                source,\n                project,\n                created_at,\n                stv_frequency,\n                stv_confidence,\n                1 - (embedding <=> %s::vector) AS vec_similarity,\n                0.0 AS keyword_boost,\n                GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (NOW() - GREATEST(created_at, COALESCE(updated_at, created_at)))) / (90 * 86400.0)) AS time_decay\n            FROM brain.thoughts\n            WHERE user_id = %s AND embedding IS NOT NULL AND (embed_model = %s OR embed_model IS NULL)\n        )\n        SELECT *,\n            (vec_similarity * 0.85) + (keyword_boost * 0.10) + (time_decay * 0.05) AS hybrid_score\n        FROM scored\n        ORDER BY hybrid_score DESC\n        LIMIT %s\n    '

    @staticmethod
    def _make_mock_conn():
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.description = []
        mock_cur.fetchall.return_value = []
        return mock_conn, mock_cur

    def test_dedup_false_sql_is_byte_identical_to_golden(self):
        mock_conn, mock_cur = self._make_mock_conn()
        with patch.object(open_brain, "_generate_embedding", return_value=[0.1] * 768), \
             patch.object(open_brain, "emit_replay_log", return_value=1):
            open_brain.search(
                mock_conn, query=self._QUERY, user_id="user-x",
                limit=5, dedup=False,
            )

        assert mock_cur.execute.call_count == 1
        sql_text, params = mock_cur.execute.call_args_list[0][0]
        # Real byte-identical comparison: NOT normalized, NOT a substring
        # or whitespace-loose match. Any drift in the dedup=False SQL
        # template reds this assertion - mutate a single space in
        # search_sql's f-string in open_brain.search() and watch this go
        # red, which is the whole point of a golden-SQL test.
        assert sql_text == self._GOLDEN_DEDUP_FALSE_SQL, (
            "dedup=False search_sql drifted from the D7 pre-T3 golden "
            "baseline - see TestD7GoldenSqlByteStability docstring"
        )
        assert params[-1] == 5, "LIMIT must be the caller's limit, unmodified"

    def test_dedup_true_sql_differs_from_golden_and_overfetches(self):
        mock_conn, mock_cur = self._make_mock_conn()
        with patch.object(open_brain, "_generate_embedding", return_value=[0.1] * 768), \
             patch.object(open_brain, "emit_replay_log", return_value=1):
            open_brain.search(
                mock_conn, query=self._QUERY, user_id="user-x",
                limit=5, dedup=True,
            )

        sql_text, params = mock_cur.execute.call_args_list[0][0]
        # Proves the golden isn't vacuously matching both paths: dedup=True
        # must produce a genuinely DIFFERENT SQL string from the dedup=False
        # golden, so this suite actually distinguishes the two code paths.
        assert sql_text != self._GOLDEN_DEDUP_FALSE_SQL
        assert "_embedding_text" in sql_text
        assert "embedding::text" in sql_text
        expected_limit = min(5 * open_brain.DEDUP_OVERFETCH_FACTOR, open_brain.DEDUP_OVERFETCH_CAP)
        assert params[-1] == expected_limit, (
            "dedup=True must over-fetch, not use the caller's raw limit"
        )


# ─── (b) --inspect live-row fallback: SECURITY tests (mocked conn, no DB) ────


class TestInspectLiveFallbackSecurity:
    """time_travel.inspect_live — the R1 CCR-reversibility fix (§4.3).

    Both tests use a mocked connection; no DATABASE_URL required. They prove:
      1. The live-row SELECT reuses the identical _assert_in_scope predicate
         (WHERE thought_id=%s AND user_id=%s), and carries user_id.
      2. A wrong-principal _assert_in_scope RuntimeError PROPAGATES (is not
         swallowed) and the fallback never attempts a cross-scope read.
    """

    def test_reuses_assert_in_scope_and_live_select_carries_user_id_predicate(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        live_row = (
            "full raw text for the live fallback",
            "a summary",
            "insight",
            [],
            [],
            [],
            "agent-x",
            "capture",
            datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        # First fetchone() answers _assert_in_scope's "SELECT 1" (in-scope);
        # second answers the live-row SELECT.
        mock_cur.fetchone.side_effect = [(1,), live_row]

        result = time_travel.inspect_live(
            mock_conn, thought_id="brain-1-abcdef01", user_id="user-a",
        )

        assert result is not None
        assert result.source == "live"
        assert result.revision == 0
        assert result.raw_text == "full raw text for the live fallback"
        assert result.query_kind == "live"

        assert mock_cur.execute.call_count == 2, (
            "expected exactly two SELECTs: the _assert_in_scope predicate, "
            "then the live-row read"
        )
        scope_sql, scope_params = mock_cur.execute.call_args_list[0][0]
        assert "brain.thoughts" in scope_sql
        assert "user_id" in scope_sql
        assert scope_params == ("brain-1-abcdef01", "user-a")

        live_sql, live_params = mock_cur.execute.call_args_list[1][0]
        assert "brain.thoughts" in live_sql
        assert "user_id" in live_sql
        assert live_params == ("brain-1-abcdef01", "user-a"), (
            "the live-row SELECT MUST carry the identical user_id predicate"
        )

    def test_wrong_principal_runtimeerror_propagates_and_refuses_cross_scope_read(self):
        mock_conn = MagicMock()
        with patch.object(
            time_travel,
            "_assert_in_scope",
            side_effect=RuntimeError(
                "inspect_live: thought brain-1-x not in user scope (user=wrong-user)"
            ),
        ) as mock_assert:
            with pytest.raises(RuntimeError, match="not in user scope"):
                time_travel.inspect_live(
                    mock_conn, thought_id="brain-1-x", user_id="wrong-user",
                )
            mock_assert.assert_called_once_with(
                mock_conn, "brain-1-x", "wrong-user", "inspect_live"
            )
        # The RuntimeError must NOT be swallowed, and no cross-scope read may
        # be attempted: conn.cursor() (the live-row SELECT) is never reached.
        mock_conn.cursor.assert_not_called()

    def test_no_live_row_returns_none_not_a_leak(self):
        """VF_epsilon: a forgotten atom has no live row either — inspect_live
        must return None (not fabricate a row), same as inspect_latest does
        for "no version exists"."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.side_effect = [(1,), None]  # in-scope, but no live row

        result = time_travel.inspect_live(
            mock_conn, thought_id="brain-1-gone0000", user_id="user-a",
        )
        assert result is None


class TestOpInspectPiFallback:
    """Pi ``op:inspect`` dispatch (fblai-egxj9) - mirrors the CLI --inspect
    §4.3 R1 fallback inside ``open_brain._run_from_pi()``'s ``elif op ==
    "inspect":`` branch: when ``time_travel.inspect_latest`` returns None
    (captured, never snapshotted), fall back to ``time_travel.inspect_live``.
    Both tests drive the REAL ``_run_from_pi()`` dispatch end to end with
    mocked stdin / ``_connect`` / ``_get_user_id`` (no DB, no real stdin) so
    they exercise the actual production code path rather than a
    reimplementation of it. This mirrors how the (b) section above tests
    ``inspect_live`` directly; here we additionally prove the Pi dispatch
    wiring around it.
    """

    @staticmethod
    def _dispatch(monkeypatch, thought_id: str, user_id: str, mock_conn) -> None:
        payload = json.dumps({"op": "inspect", "thought_id": thought_id})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        monkeypatch.setattr(open_brain, "_get_user_id", lambda: user_id)
        monkeypatch.setattr(open_brain, "_connect", lambda: mock_conn)
        open_brain._run_from_pi()

    def test_scoping_wall_cross_principal_call_never_leaks_other_users_thought(
        self, monkeypatch, capsys,
    ):
        """THE CRUX (Harvey gate #1): user A calls op:inspect for a thought
        id that ``inspect_latest`` has no version for (never snapshotted),
        so the T3 fallback fires -> ``inspect_live`` -> inspect_live's OWN
        ``_assert_in_scope`` call (belt-and-suspenders, independent of
        inspect_latest's own scope check, which is bypassed here so this
        test isolates coverage to inspect_live's redundant check) raises
        RuntimeError for the cross-principal call. There is no local
        try/except around ``elif op == "inspect":`` in ``_run_from_pi()``
        (only the outer ``finally: conn.close()``), so the RuntimeError
        propagates out uncaught - A's op:inspect call NEVER prints B's (or
        anyone's) raw_text/summary; it errors out instead.

        PROBE: remove the ``_assert_in_scope`` call inside
        ``time_travel.inspect_live`` and this test REDS - the RuntimeError
        would no longer fire and the live-row SELECT would proceed (this
        is exactly the scoping hole the fallback must never open).
        """
        mock_conn = MagicMock()
        with patch.object(time_travel, "inspect_latest", return_value=None), \
             patch.object(
                 time_travel,
                 "_assert_in_scope",
                 side_effect=RuntimeError(
                     "inspect_live: thought brain-1-b not in user scope (user=user-a)"
                 ),
             ):
            with pytest.raises(RuntimeError, match="not in user scope"):
                self._dispatch(monkeypatch, "brain-1-b", "user-a", mock_conn)

        out = capsys.readouterr().out
        assert out == "", (
            "no JSON payload of any kind may be printed on the walled path "
            f"(got: {out!r})"
        )
        # inspect_live's live-row SELECT is never reached: _assert_in_scope
        # raised before any cursor was ever opened.
        mock_conn.cursor.assert_not_called()
        # The outer `finally: conn.close()` still runs even though the
        # exception propagates.
        mock_conn.close.assert_called_once()

    def test_fallback_fires_for_owner_never_snapshotted_returns_live_row(
        self, monkeypatch, capsys,
    ):
        """Owner path (mirrors the CLI test's setup): user A captured a
        thought that was never snapshotted (no thought_versions row, so
        inspect_latest returns None). op:inspect with no --at/--at_revision
        qualifier must fall back to inspect_live() and return A's own live
        row - result is not None and carries A's raw_text."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        live_row = (
            "A's own live raw text, never snapshotted",
            "a live summary",
            "insight",
            [],
            [],
            [],
            "agent-a",
            "capture",
            datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        # _assert_in_scope's "SELECT 1" (A is in scope), then the live-row SELECT.
        mock_cur.fetchone.side_effect = [(1,), live_row]

        with patch.object(time_travel, "inspect_latest", return_value=None):
            self._dispatch(monkeypatch, "brain-1-a", "user-a", mock_conn)

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload.get("thought_id") == "brain-1-a"
        assert payload.get("raw_text") == "A's own live raw text, never snapshotted"
        assert payload.get("source") == "live", (
            "the fallback result must be tagged source='live', not a "
            "fabricated thought_versions row"
        )
        mock_conn.close.assert_called_once()


# ─── Integration tests (DATABASE_URL-gated; skip cleanly without it) ─────────


def _get_conn_or_skip():
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


def _cleanup_user(conn, uid: str) -> None:
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


@pytest.fixture(scope="module")
def conn():
    c = _get_conn_or_skip()
    yield c
    c.close()


@pytest.fixture()
def test_user(conn):
    uid = f"test-dedup-{uuid.uuid4().hex[:10]}"
    yield uid
    _cleanup_user(conn, uid)


class TestR1RoundTripIntegration:
    """R1 (§4.4): capture -> --search --dedup --json -> take any THOUGHT_ID
    from results/NEAR_DUPLICATE_IDS -> --inspect <id> returns non-empty
    raw_text. Exercises the §4.3 live-row fallback end to end.
    """

    def test_search_dedup_to_inspect_round_trip(self, conn, test_user):
        text = "R1 CCR round trip unique marker zxcvbn789 alpha"
        cap = open_brain.capture(conn, text=text, user_id=test_user, source="test")
        tid = cap["thought_id"]

        results = open_brain.search(
            conn, query=text, user_id=test_user, limit=5, dedup=True,
        )
        assert results, "expected at least one result for the captured thought"

        all_reachable_ids = set()
        for r in results:
            all_reachable_ids.add(r["THOUGHT_ID"])
            all_reachable_ids.update(r.get("NEAR_DUPLICATE_IDS", []) or [])
        assert tid in all_reachable_ids, (
            "captured thought must be reachable from the deduped result set "
            "(either as a survivor or inside NEAR_DUPLICATE_IDS)"
        )

        # inspect_latest returns None -- this is the verified R1 gap: a
        # captured-but-never-snapshotted atom has no thought_versions row.
        latest = time_travel.inspect_latest(conn, thought_id=tid, user_id=test_user)
        assert latest is None

        # The fallback closes the gap: full raw_text, tagged "live".
        live = time_travel.inspect_live(conn, thought_id=tid, user_id=test_user)
        assert live is not None
        assert live.raw_text
        assert text in live.raw_text
        assert live.source == "live"

    def test_inspect_live_wrong_principal_refuses_against_real_db(self, conn, test_user):
        text = "R1 wrong principal refusal test marker uiop456"
        cap = open_brain.capture(conn, text=text, user_id=test_user, source="test")
        tid = cap["thought_id"]

        other_uid = f"test-dedup-other-{uuid.uuid4().hex[:8]}"
        with pytest.raises(RuntimeError, match="not in user scope"):
            time_travel.inspect_live(conn, thought_id=tid, user_id=other_uid)


class TestD9ReinforcementScopeIntegration:
    """D9 (§3.8): updated_at is touched ONLY for returned survivors, never
    for collapsed atoms. Captures the SAME text twice (deterministic
    embeddings -> cosine ~1.0, guaranteed >= DEDUP_COSINE) so one capture
    collapses into the other, then asserts the collapsed atom's updated_at
    is untouched while the survivor's advances.
    """

    def test_reinforcement_touches_survivor_not_collapsed_atom(self, conn, test_user):
        text = "D9 reinforcement scope test unique marker qwerty123"
        r1 = open_brain.capture(conn, text=text, user_id=test_user, source="test")
        r2 = open_brain.capture(conn, text=text, user_id=test_user, source="test")
        tid1, tid2 = r1["thought_id"], r2["thought_id"]

        cur = conn.cursor()
        cur.execute(
            "SELECT thought_id, updated_at FROM brain.thoughts "
            "WHERE thought_id IN (%s, %s)",
            (tid1, tid2),
        )
        before = dict(cur.fetchall())
        cur.close()

        results = open_brain.search(
            conn, query=text, user_id=test_user, limit=5, dedup=True,
        )
        assert results
        survivor = next(
            (r for r in results if r["THOUGHT_ID"] in (tid1, tid2)), None
        )
        assert survivor is not None, "one of the two identical captures must survive"
        assert survivor.get("NEAR_DUPLICATE_COUNT", 0) >= 1, (
            "the two identical captures should have collapsed into one survivor"
        )
        collapsed_id = survivor["NEAR_DUPLICATE_IDS"][0]
        survivor_id = survivor["THOUGHT_ID"]
        assert {survivor_id, collapsed_id} == {tid1, tid2}

        cur = conn.cursor()
        cur.execute(
            "SELECT thought_id, updated_at FROM brain.thoughts "
            "WHERE thought_id IN (%s, %s)",
            (tid1, tid2),
        )
        after = dict(cur.fetchall())
        cur.close()

        assert after[survivor_id] > before[survivor_id], (
            "survivor's updated_at must be touched by the reinforcement pass"
        )
        assert after[collapsed_id] == before[collapsed_id], (
            "collapsed atom's updated_at must NOT be touched (D9)"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ─── Review fold I-1/M-1: deterministic composite survivor tie-break ──────────


def test_i1_tied_hybrid_score_survivor_deterministic_by_stv_c():
    """I-1/M-1 (§3.5): on EQUAL HYBRID_SCORE, the composite tie-break (STV.c DESC)
    selects the SAME survivor regardless of input order. Pre-fix, tied atoms
    retained arbitrary SQL order so the survivor (and its reinforcement) flipped."""
    import open_brain as ob

    def _atom(tid, c):
        return {"THOUGHT_ID": tid, "HYBRID_SCORE": 0.5000, "STV": {"f": 1.0, "c": c},
                "CREATED_AT": "2026-07-02T00:00:00", "_EMBEDDING": [1.0, 0.0]}

    a_hi = _atom("brain-1-aaaaaaaa", 0.9)   # higher STV.c -> must survive
    a_lo = _atom("brain-2-bbbbbbbb", 0.1)
    for order in ([a_hi, a_lo], [a_lo, a_hi]):   # both input orders
        rows = [dict(x) for x in order]
        ob._dedup_composite_sort(rows)
        out = ob._semantic_dedup(rows)
        assert len(out) == 1
        assert out[0]["THOUGHT_ID"] == "brain-1-aaaaaaaa"   # deterministic, order-independent
        assert out[0]["NEAR_DUPLICATE_IDS"] == ["brain-2-bbbbbbbb"]
