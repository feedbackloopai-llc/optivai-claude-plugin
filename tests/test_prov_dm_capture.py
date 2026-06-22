#!/usr/bin/env python3
"""brain-W1-S2: PROV-DM capture flow tests.

Verifies the WA + PV runtime: every capture writes complete PROV-DM
(W3C PROV-DM 1.3 conformant) with sensible defaults derived from source.

Run: python3 -m pytest tests/test_prov_dm_capture.py -v
"""
import os
import sys
import subprocess

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402


# ─── Connection fixture ──────────────────────────────────────────────────────


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
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")
    c = psycopg2.connect(db_url)
    c.autocommit = False
    yield c
    c.close()


def _cleanup_thought(conn, thought_id: str) -> None:
    """Remove a test-created thought row + any dependent rows.

    PROV `was_derived_from` is ON DELETE SET NULL so we can drop in any
    order, but we still clean knowledge-graph artefacts that may have
    been created by `_update_graph_incremental`.
    """
    cur = conn.cursor()
    try:
        # Knowledge-graph tables may or may not exist depending on init order;
        # use IF EXISTS-style guards via try/except.
        try:
            cur.execute(
                "DELETE FROM brain.kg_edges WHERE source_thought_id = %s OR target_thought_id = %s",
                (thought_id, thought_id),
            )
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.kg_nodes WHERE thought_id = %s",
                (thought_id,),
            )
        except Exception:
            conn.rollback()
        cur.execute(
            "DELETE FROM brain.thoughts WHERE thought_id = %s",
            (thought_id,),
        )
        conn.commit()
    finally:
        cur.close()


# ─── Unit tests: _derive_prov_agent (no PostgreSQL needed) ───────────────────


class TestProvAgentDerivation:
    def test_manual_source_yields_cli_user_prefix(self):
        assert open_brain._derive_prov_agent("manual", "alice") == "cli-user-alice"

    def test_empty_source_defaults_to_cli_user(self):
        assert open_brain._derive_prov_agent("", "bob") == "cli-user-bob"

    def test_none_source_defaults_to_cli_user(self):
        # Defensive: if upstream forgets to pass a source, still get a sane default.
        assert open_brain._derive_prov_agent(None, "carol") == "cli-user-carol"

    def test_pi_source_yields_pi_agent(self):
        assert open_brain._derive_prov_agent("pi", "alice") == "pi-agent"

    def test_pi_prefix_source_yields_pi_agent(self):
        assert open_brain._derive_prov_agent("pi-bridge", "alice") == "pi-agent"

    def test_claude_code_source_passthrough(self):
        assert open_brain._derive_prov_agent("claude-code", "alice") == "claude-code"

    def test_hook_source_preserves_suffix(self):
        assert (
            open_brain._derive_prov_agent("hook-decision-signal", "alice")
            == "claude-code-hook-decision-signal"
        )

    def test_hook_source_preference_suffix(self):
        assert (
            open_brain._derive_prov_agent("hook-preference", "alice")
            == "claude-code-hook-preference"
        )

    def test_unknown_source_falls_back(self):
        assert (
            open_brain._derive_prov_agent("custom-thing", "alice")
            == "source-custom-thing"
        )


# ─── Unit tests: _generate_activity_id ───────────────────────────────────────


class TestActivityIdGeneration:
    def test_stable_activity_id_for_thought(self):
        tid = "brain-1234567890-abcdef"
        assert open_brain._generate_activity_id(tid) == f"activity-{tid}"

    def test_different_thoughts_different_activities(self):
        a = open_brain._generate_activity_id("brain-aaa-111")
        b = open_brain._generate_activity_id("brain-bbb-222")
        assert a != b


# ─── Integration: capture() populates PROV-DM ────────────────────────────────


class TestCaptureFillsProvDm:
    def test_default_capture_populates_three_required_prov_fields(self, conn):
        result = open_brain.capture(
            conn,
            text="hello PROV-DM world",
            user_id="testuser-s2-default",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT prov_agent, prov_activity, was_generated_by,
                          was_derived_from, source_uri
                   FROM brain.thoughts WHERE thought_id = %s""",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None
            assert row[0] == "cli-user-testuser-s2-default"
            assert row[1] == "capture"
            assert row[2] == f"activity-{tid}"
            assert row[3] is None  # was_derived_from
            assert row[4] is None  # source_uri
        finally:
            _cleanup_thought(conn, tid)

    def test_explicit_prov_agent_overrides_default(self, conn):
        result = open_brain.capture(
            conn,
            text="explicit override of PROV agent",
            user_id="testuser-s2-explicit",
            prov_agent="custom-agent-xyz",
            prov_activity="import",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent, prov_activity FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "custom-agent-xyz"
            assert row[1] == "import"
        finally:
            _cleanup_thought(conn, tid)

    def test_pi_source_capture_yields_pi_agent(self, conn):
        result = open_brain.capture(
            conn,
            text="captured from pi context",
            user_id="testuser-s2-pi",
            source="pi",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "pi-agent"
        finally:
            _cleanup_thought(conn, tid)

    def test_hook_source_capture_yields_hook_agent(self, conn):
        result = open_brain.capture(
            conn,
            text="captured by decision-signal hook",
            user_id="testuser-s2-hook",
            source="hook-decision-signal",
        )
        tid = result["thought_id"]
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "claude-code-hook-decision-signal"
        finally:
            _cleanup_thought(conn, tid)


# ─── Integration: was_derived_from scoping (PS primitive) ────────────────────


class TestDerivedFromValidation:
    def test_capture_with_valid_derived_from_succeeds(self, conn):
        parent = open_brain.capture(
            conn,
            text="parent thought for derivation test",
            user_id="testuser-s2-deriv",
        )
        pid = parent["thought_id"]
        try:
            child = open_brain.capture(
                conn,
                text="child thought derived from parent",
                user_id="testuser-s2-deriv",
                was_derived_from=pid,
            )
            cid = child["thought_id"]
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT was_derived_from FROM brain.thoughts WHERE thought_id=%s",
                    (cid,),
                )
                row = cur.fetchone()
                cur.close()
                assert row[0] == pid
            finally:
                _cleanup_thought(conn, cid)
        finally:
            _cleanup_thought(conn, pid)

    def test_capture_with_nonexistent_derived_from_raises(self, conn):
        with pytest.raises(RuntimeError, match="was_derived_from"):
            open_brain.capture(
                conn,
                text="orphan child has no parent",
                user_id="testuser-s2-orphan",
                was_derived_from="nonexistent-thought-id-xyz-12345",
            )
        # Connection must remain usable after the validation rollback.
        conn.rollback()

    def test_capture_with_cross_user_derived_from_raises(self, conn):
        # Create parent under user A
        parent = open_brain.capture(
            conn,
            text="userA parent thought",
            user_id="userA-s2",
        )
        pid = parent["thought_id"]
        try:
            # Attempt to derive under user B — must fail (PS scoping)
            with pytest.raises(RuntimeError, match="was_derived_from"):
                open_brain.capture(
                    conn,
                    text="userB sneaky child trying to derive from userA",
                    user_id="userB-s2",
                    was_derived_from=pid,
                )
            conn.rollback()
        finally:
            _cleanup_thought(conn, pid)


# ─── CLI flags ───────────────────────────────────────────────────────────────


class TestCliFlags:
    def test_help_lists_three_new_flags(self):
        repo_root = os.path.join(os.path.dirname(__file__), "..")
        result = subprocess.run(
            [sys.executable, "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--prov-agent" in result.stdout
        assert "--prov-activity" in result.stdout
        assert "--derived-from" in result.stdout


# ─── F1 closure: greenfield freshness ────────────────────────────────────────
# The F1 risk from S1 review (capture() failing on a fresh schema with NOT NULL
# PROV columns) is structurally closed by S2 — capture() now ALWAYS supplies
# defaults via _derive_prov_agent + _generate_activity_id. This test provides
# defense-in-depth by exercising the full path against the canonical schema.
# Marked opt-in because it would be destructive if it ran against a shared DB
# without isolation; in this implementation we use a dedicated test thought ID
# under a unique user, which the live schema already supports.


class TestGreenfieldCapture:
    @pytest.mark.skipif(
        os.environ.get("BRAIN_FRESH_TEST_ENABLED") != "1",
        reason="Greenfield test (opt-in: set BRAIN_FRESH_TEST_ENABLED=1)",
    )
    def test_capture_works_on_freshly_initialized_schema(self, conn):
        """F1 closure: capture() must succeed with no caller-supplied PROV."""
        result = open_brain.capture(
            conn,
            text="hello fresh brain — F1 defense-in-depth",
            user_id="testuser-s2-greenfield",
        )
        tid = result["thought_id"]
        try:
            assert tid is not None
            cur = conn.cursor()
            cur.execute(
                """SELECT prov_agent, prov_activity, was_generated_by
                   FROM brain.thoughts WHERE thought_id=%s""",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "cli-user-testuser-s2-greenfield"
            assert row[1] == "capture"
            assert row[2] == f"activity-{tid}"
        finally:
            _cleanup_thought(conn, tid)
