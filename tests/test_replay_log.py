#!/usr/bin/env python3
"""brain-W2-S5+S6+S7+S8: Replay log tests.

Verifies:
  - Schema (S5): brain.replay_log table + indexes + pii_distinct DEFAULT TRUE
  - Instrumentation (S6): capture / forget / snapshot / rollback / promote /
    demote each emit a replay row before returning
  - CLI (S7): --replay --from/--to/--event-type/--session-id/--json returns
    chronological per-user audit trail
  - Test corpus (S8): coverage of all above plus PII redaction at the boundary,
    OTel correlation from env, and best-effort error swallowing

PII redaction: emails, phones, SSNs, card numbers are masked at the emitter
boundary BEFORE writing. Audit rows never contain raw PII.

OTel correlation: if OTEL_TRACE_ID / OTEL_SPAN_ID env vars are set, the values
are captured on every emitted row.

Run: DATABASE_URL=postgres://... python3 -m pytest tests/test_replay_log.py -v
"""
import json
import os
import subprocess
import sys
from typing import Optional

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "sql", "migrations", "2026-05-21-replay-log.sql"
)


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


@pytest.fixture(scope="module", autouse=True)
def _run_replay_migration(conn):
    """Apply the replay-log migration once before any test runs (idempotent).

    Skips silently if the migration file is not yet present so the failing-test
    phase of TDD reports clean skip rather than fixture errors.
    """
    if not os.path.exists(MIGRATION_PATH):
        pytest.skip(f"Migration file missing: {MIGRATION_PATH}")
    with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _count_log_rows(conn, user_id: str, event_type: Optional[str] = None) -> int:
    cur = conn.cursor()
    try:
        if event_type:
            cur.execute(
                "SELECT COUNT(*) FROM brain.replay_log "
                "WHERE user_id=%s AND event_type=%s",
                (user_id, event_type),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM brain.replay_log WHERE user_id=%s",
                (user_id,),
            )
        return cur.fetchone()[0]
    finally:
        cur.close()


def _cleanup_logs(conn, user_id: str):
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM brain.replay_log WHERE user_id=%s", (user_id,))
        conn.commit()
    finally:
        cur.close()


def _cleanup_thought(conn, thought_id: str):
    """Best-effort row teardown. CASCADE removes related promotions + versions."""
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM brain.thoughts WHERE thought_id=%s",
            (thought_id,),
        )
        conn.commit()
    finally:
        cur.close()


# ─── S5: Schema ──────────────────────────────────────────────────────────────


class TestReplayLogSchema:
    def test_replay_log_table_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='brain' AND table_name='replay_log'
            """
        )
        assert cur.fetchone() is not None
        cur.close()

    def test_required_columns_present(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='replay_log'
            """
        )
        cols = {r[0] for r in cur.fetchall()}
        cur.close()
        for required in (
            "event_id", "user_id", "session_id", "event_type",
            "thought_id", "query_redacted", "result_summary",
            "pii_distinct", "trace_id", "span_id",
            "prov_agent", "metadata", "created_at",
        ):
            assert required in cols, f"missing column: {required}"

    def test_pii_distinct_column_defaults_true(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='replay_log'
              AND column_name='pii_distinct'
            """
        )
        default = cur.fetchone()[0]
        cur.close()
        assert default is not None and "true" in default.lower()

    def test_session_index_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='replay_log'
              AND indexname='idx_replay_log_session'
            """
        )
        assert cur.fetchone() is not None
        cur.close()

    def test_user_time_index_exists(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname='brain' AND tablename='replay_log'
              AND indexname='idx_replay_log_user_time'
            """
        )
        assert cur.fetchone() is not None
        cur.close()


# ─── S6: PII Redaction ───────────────────────────────────────────────────────


class TestPiiRedaction:
    def test_email_redacted(self):
        result = open_brain.redact_pii("contact me at alice@example.com")
        assert "[EMAIL]" in result
        assert "alice@example.com" not in result

    def test_phone_redacted(self):
        result = open_brain.redact_pii("Call 555-867-5309 anytime")
        assert "[PHONE]" in result
        assert "555-867-5309" not in result

    def test_ssn_redacted(self):
        result = open_brain.redact_pii("SSN: 123-45-6789 on file")
        assert "[SSN]" in result
        assert "123-45-6789" not in result

    def test_card_redacted(self):
        result = open_brain.redact_pii("Visa: 4111 1111 1111 1111")
        assert "[CARD]" in result
        assert "4111 1111 1111 1111" not in result

    def test_no_pii_passes_through(self):
        assert open_brain.redact_pii("hello world") == "hello world"

    def test_none_returns_none(self):
        assert open_brain.redact_pii(None) is None

    def test_empty_string_returns_empty(self):
        assert open_brain.redact_pii("") == ""


# ─── S6: capture() instrumentation ──────────────────────────────────────────


class TestCaptureInstrumentation:
    def test_capture_emits_replay_row(self, conn):
        u = "replay-cap"
        _cleanup_logs(conn, u)
        before = _count_log_rows(conn, u, "capture")
        r = open_brain.capture(conn, text="hello replay world", user_id=u)
        tid = r["thought_id"]
        try:
            after = _count_log_rows(conn, u, "capture")
            assert after == before + 1
            cur = conn.cursor()
            cur.execute(
                """
                SELECT event_type, thought_id, result_summary, pii_distinct
                FROM brain.replay_log WHERE user_id=%s
                ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "capture"
            assert row[1] == tid
            assert "hello replay" in row[2]
            assert row[3] is True
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_capture_with_pii_redacts_in_log(self, conn):
        """A capture containing an email gets the [EMAIL] token in the replay log,
        NOT the raw email."""
        u = "replay-cap-pii"
        _cleanup_logs(conn, u)
        r = open_brain.capture(
            conn,
            text="my email is bob@example.com call me",
            user_id=u,
        )
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT result_summary FROM brain.replay_log
                WHERE user_id=%s ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            summary = cur.fetchone()[0]
            cur.close()
            assert "bob@example.com" not in summary
            assert "[EMAIL]" in summary
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_capture_summary_truncated_to_100_chars(self, conn):
        u = "replay-cap-trunc"
        _cleanup_logs(conn, u)
        long_text = "x" * 500
        r = open_brain.capture(conn, text=long_text, user_id=u)
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT result_summary FROM brain.replay_log
                WHERE user_id=%s ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            summary = cur.fetchone()[0]
            cur.close()
            assert len(summary) <= 100
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)


# ─── S6: forget / snapshot / rollback / promote / demote ─────────────────────


class TestOtherOpsInstrumentation:
    def test_forget_emits_replay_row(self, conn):
        u = "replay-forget"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="forget test", user_id=u)
        tid = r["thought_id"]
        # capture itself emitted 1 row; forget should add another
        try:
            open_brain.forget_thought(conn, tid, u, n=30)
            assert _count_log_rows(conn, u, "forget") >= 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_snapshot_emits_replay_row(self, conn):
        u = "replay-snap"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="snap test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, u)
            assert _count_log_rows(conn, u, "snapshot") >= 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_rollback_emits_replay_row(self, conn):
        u = "replay-rb"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="rb test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, u)
            open_brain.rollback_thought(conn, tid, u, to_revision=1)
            assert _count_log_rows(conn, u, "rollback") >= 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_promote_emits_replay_row(self, conn):
        u = "replay-prom"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="promote test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, u, weight=2.0)
            assert _count_log_rows(conn, u, "promote") >= 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_demote_emits_replay_row(self, conn):
        u = "replay-dem"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="demote test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.demote_thought(conn, tid, u, weight=1.0)
            assert _count_log_rows(conn, u, "demote") >= 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_rollback_metadata_records_target_revision(self, conn):
        u = "replay-rb-meta"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="rb meta test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, u)
            open_brain.rollback_thought(conn, tid, u, to_revision=1)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT metadata FROM brain.replay_log
                WHERE user_id=%s AND event_type='rollback'
                ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            md = cur.fetchone()[0]
            cur.close()
            # psycopg2 returns JSONB as dict
            if isinstance(md, str):
                md = json.loads(md)
            assert md is not None
            assert md.get("rolled_back_to_revision") == 1
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)


# ─── S6.1: search instrumentation (gz-woema) ─────────────────────────────────


class TestSearchInstrumentation:
    """search() emits one replay row per invocation. Query is PII-redacted at
    the emitter boundary; metadata captures result_count, top_thought_id, and
    filter usage."""

    def test_search_emits_replay_row(self, conn):
        u = "replay-search"
        _cleanup_logs(conn, u)
        # Capture a thought we know we can search for
        r = open_brain.capture(conn, text="distinctive purple llama runs in the rain", user_id=u)
        tid = r["thought_id"]
        try:
            results = open_brain.search(conn, query="purple llama", user_id=u, limit=5)
            cur = conn.cursor()
            cur.execute(
                "SELECT event_type, query_redacted FROM brain.replay_log "
                "WHERE user_id=%s AND event_type='search' ORDER BY event_id DESC LIMIT 1",
                (u,),
            )
            row = cur.fetchone()
            assert row is not None, "search() did not emit a replay log row"
            assert row[0] == "search"
            # Query is preserved (no PII to redact in this case)
            assert "purple llama" in (row[1] or "")
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_search_with_pii_redacts_query(self, conn):
        """A query containing an email gets [EMAIL] in the replay log, not the raw address."""
        u = "replay-search-pii"
        _cleanup_logs(conn, u)
        open_brain.search(conn, query="who is bob@example.com", user_id=u, limit=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT query_redacted FROM brain.replay_log "
            "WHERE user_id=%s AND event_type='search' ORDER BY event_id DESC LIMIT 1",
            (u,),
        )
        row = cur.fetchone()
        assert row is not None
        assert "bob@example.com" not in (row[0] or "")
        assert "[EMAIL]" in (row[0] or "")
        _cleanup_logs(conn, u)

    def test_search_metadata_records_result_count(self, conn):
        u = "replay-search-meta"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="metadata recording test alpha beta gamma", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.search(conn, query="alpha beta gamma", user_id=u, limit=10)
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata FROM brain.replay_log "
                "WHERE user_id=%s AND event_type='search' ORDER BY event_id DESC LIMIT 1",
                (u,),
            )
            md_raw = cur.fetchone()[0]
            md = md_raw if isinstance(md_raw, dict) else json.loads(md_raw)
            assert "result_count" in md
            assert md["limit"] == 10
            assert md["has_filters"] is False  # no filters used
            assert "sort_by" in md
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_search_metadata_records_filter_usage(self, conn):
        u = "replay-search-filters"
        _cleanup_logs(conn, u)
        open_brain.search(conn, query="filtered query", user_id=u, limit=5,
                           thought_type="decision")
        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM brain.replay_log "
            "WHERE user_id=%s AND event_type='search' ORDER BY event_id DESC LIMIT 1",
            (u,),
        )
        md_raw = cur.fetchone()[0]
        md = md_raw if isinstance(md_raw, dict) else json.loads(md_raw)
        assert md["has_filters"] is True
        _cleanup_logs(conn, u)

    def test_search_zero_results_still_emits(self, conn):
        """A search that returns nothing still emits a replay row with result_count=0."""
        u = "replay-search-empty"
        _cleanup_logs(conn, u)
        open_brain.search(conn, query="extremely-unlikely-phrase-zzzqqq-9999", user_id=u, limit=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT result_summary, metadata FROM brain.replay_log "
            "WHERE user_id=%s AND event_type='search' ORDER BY event_id DESC LIMIT 1",
            (u,),
        )
        row = cur.fetchone()
        assert row is not None
        assert "0 results" in (row[0] or "")
        md = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        assert md["result_count"] == 0
        assert md["top_thought_id"] is None
        _cleanup_logs(conn, u)


# ─── S7: CLI surface ─────────────────────────────────────────────────────────


class TestReplayCli:
    def test_help_includes_replay_flags(self):
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True, text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, f"--help failed: {result.stderr}"
        for flag in ("--replay", "--from", "--to", "--event-type"):
            assert flag in result.stdout, f"{flag} missing in --help"

    def test_replay_cli_returns_user_chronology(self, conn):
        u = "replay-cli-chrono"
        _cleanup_logs(conn, u)
        r1 = open_brain.capture(conn, text="first thought", user_id=u)
        r2 = open_brain.capture(conn, text="second thought", user_id=u)
        try:
            env = {**os.environ, "USER": u}
            # Strip OTEL vars so this test doesn't pick up trace IDs from
            # the env (the OTel-correlation test sets them explicitly).
            env.pop("OTEL_TRACE_ID", None)
            env.pop("OTEL_SPAN_ID", None)
            result = subprocess.run(
                ["python3", "scripts/open_brain.py", "--replay", "--json"],
                capture_output=True, text=True,
                cwd=REPO_ROOT,
                env=env,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            data = json.loads(result.stdout)
            captures = [row for row in data if row["event_type"] == "capture"]
            assert len(captures) >= 2
            tids = [c["thought_id"] for c in captures]
            assert r1["thought_id"] in tids
            assert r2["thought_id"] in tids
        finally:
            _cleanup_thought(conn, r1["thought_id"])
            _cleanup_thought(conn, r2["thought_id"])
            _cleanup_logs(conn, u)

    def test_replay_cli_filter_by_event_type(self, conn):
        u = "replay-cli-filter"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="filter test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, u, weight=1.0)
            env = {**os.environ, "USER": u}
            result = subprocess.run(
                ["python3", "scripts/open_brain.py", "--replay",
                 "--event-type", "promote", "--json"],
                capture_output=True, text=True,
                cwd=REPO_ROOT,
                env=env,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            data = json.loads(result.stdout)
            assert len(data) >= 1
            for row in data:
                assert row["event_type"] == "promote"
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)


# ─── S6: OTel correlation ────────────────────────────────────────────────────


class TestOtelCorrelation:
    def test_otel_trace_id_captured_when_set(self, conn, monkeypatch):
        u = "replay-otel"
        _cleanup_logs(conn, u)
        monkeypatch.setenv("OTEL_TRACE_ID", "test-trace-id-12345")
        monkeypatch.setenv("OTEL_SPAN_ID", "test-span-id-67890")
        r = open_brain.capture(conn, text="otel test", user_id=u)
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT trace_id, span_id FROM brain.replay_log
                WHERE user_id=%s ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "test-trace-id-12345"
            assert row[1] == "test-span-id-67890"
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)

    def test_otel_absent_when_env_unset(self, conn, monkeypatch):
        u = "replay-otel-none"
        _cleanup_logs(conn, u)
        monkeypatch.delenv("OTEL_TRACE_ID", raising=False)
        monkeypatch.delenv("OTEL_SPAN_ID", raising=False)
        r = open_brain.capture(conn, text="no otel", user_id=u)
        tid = r["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT trace_id, span_id FROM brain.replay_log
                WHERE user_id=%s ORDER BY event_id DESC LIMIT 1
                """,
                (u,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] is None
            assert row[1] is None
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)


# ─── S6: best-effort discipline (audit never blocks the user-facing op) ──────


class TestEmitterFailureSwallowing:
    """Best-effort discipline: a replay log failure must NOT block the user-facing op."""

    def test_emit_returns_minus_one_on_db_error(self):
        u = "replay-emit-err"
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not set")
        bad_conn = psycopg2.connect(db_url)
        bad_conn.close()
        # Closed connection: emit_replay_log must NOT raise; must return -1.
        try:
            eid = open_brain.emit_replay_log(
                bad_conn, user_id=u, event_type="capture",
            )
        except Exception as e:  # pragma: no cover — guard for the discipline
            pytest.fail(
                f"emit_replay_log must swallow errors, not raise: {e}"
            )
        assert eid == -1

    def test_emit_swallows_when_bound_conn_in_aborted_state(self, conn):
        """A failed prior statement must not poison a subsequent emit call.

        The emitter calls conn.rollback() in its except handler so the next
        caller is not stuck in a failed-transaction state.
        """
        u = "replay-emit-abort"
        cur = conn.cursor()
        try:
            try:
                cur.execute("SELECT * FROM brain.nonexistent_table_xyz")
            except Exception:
                pass
            # Connection is now in a failed-tx state. emit must swallow.
            eid = open_brain.emit_replay_log(
                conn, user_id=u, event_type="capture",
            )
            assert eid == -1
        finally:
            cur.close()
            try:
                conn.rollback()
            except Exception:
                pass


# ─── S7: query_replay_log helper ─────────────────────────────────────────────


class TestQueryReplayLog:
    def test_query_returns_user_scoped_rows(self, conn):
        u1, u2 = "replay-query-a", "replay-query-b"
        _cleanup_logs(conn, u1)
        _cleanup_logs(conn, u2)
        r1 = open_brain.capture(conn, text="user a thought", user_id=u1)
        r2 = open_brain.capture(conn, text="user b thought", user_id=u2)
        try:
            rows_a = open_brain.query_replay_log(conn, user_id=u1)
            assert all(r["user_id"] == u1 for r in rows_a)
            assert len(rows_a) >= 1
            rows_b = open_brain.query_replay_log(conn, user_id=u2)
            assert all(r["user_id"] == u2 for r in rows_b)
            assert len(rows_b) >= 1
        finally:
            _cleanup_thought(conn, r1["thought_id"])
            _cleanup_thought(conn, r2["thought_id"])
            _cleanup_logs(conn, u1)
            _cleanup_logs(conn, u2)

    def test_query_filter_by_event_type(self, conn):
        u = "replay-query-evt"
        _cleanup_logs(conn, u)
        r = open_brain.capture(conn, text="evt test", user_id=u)
        tid = r["thought_id"]
        try:
            open_brain.promote_thought(conn, tid, u, weight=1.0)
            rows = open_brain.query_replay_log(
                conn, user_id=u, event_type="promote",
            )
            assert len(rows) >= 1
            assert all(r["event_type"] == "promote" for r in rows)
        finally:
            _cleanup_thought(conn, tid)
            _cleanup_logs(conn, u)
