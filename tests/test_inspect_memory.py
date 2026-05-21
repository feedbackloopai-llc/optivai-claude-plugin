"""brain-W2-S3+S4: Inspect-memory (time-travel) tests.

Verifies historical-state queries against ``brain.thought_versions``:
  - by ISO timestamp (returns version with largest created_at <= target)
  - by revision number (exact match)
  - latest (max revision)

PS scoping enforced. Missing-version returns ``None`` (not error).
RB invariant exposed: rollback appends a new revision; querying the
original revision still returns the original data.

Run: python3 -m pytest tests/test_inspect_memory.py -v
"""
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402
import time_travel  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


def _cleanup(conn, *tids):
    """Delete a thought (cascades to versions via FK ON DELETE CASCADE)."""
    cur = conn.cursor()
    try:
        for tid in tids:
            # Knowledge-graph artefacts (best-effort).
            try:
                cur.execute(
                    "DELETE FROM brain.kg_edges WHERE source_thought_id=%s OR target_thought_id=%s",
                    (tid, tid),
                )
            except Exception:
                conn.rollback()
            try:
                cur.execute(
                    "DELETE FROM brain.kg_nodes WHERE thought_id=%s",
                    (tid,),
                )
            except Exception:
                conn.rollback()
            cur.execute("DELETE FROM brain.thoughts WHERE thought_id=%s", (tid,))
        conn.commit()
    finally:
        cur.close()


# ─── inspect_at_revision ────────────────────────────────────────────────────


class TestInspectAtRevision:
    def test_inspect_existing_revision(self, conn):
        r = open_brain.capture(conn, text="v1 text", user_id="insp-rev")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-rev")  # revision 1
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='v2 text' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            cur.close()
            open_brain.snapshot_thought(conn, tid, "insp-rev")  # revision 2

            result = time_travel.inspect_at_revision(conn, tid, "insp-rev", revision=1)
            assert result is not None
            assert result.revision == 1
            assert result.raw_text == "v1 text"
            assert result.query_kind == "at-revision"
            assert result.query_value == "1"
        finally:
            _cleanup(conn, tid)

    def test_inspect_nonexistent_revision_returns_none(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-rev-bad")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-rev-bad")  # rev 1
            result = time_travel.inspect_at_revision(
                conn, tid, "insp-rev-bad", revision=99
            )
            assert result is None
        finally:
            _cleanup(conn, tid)

    def test_inspect_revision_cross_user_rejected(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-rev-a")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-rev-a")
            with pytest.raises(RuntimeError, match="not in user scope"):
                time_travel.inspect_at_revision(
                    conn, tid, "insp-rev-b", revision=1
                )
        finally:
            _cleanup(conn, tid)


# ─── inspect_at_timestamp ───────────────────────────────────────────────────


class TestInspectAtTimestamp:
    def test_inspect_returns_latest_before_timestamp(self, conn):
        r = open_brain.capture(conn, text="orig text", user_id="insp-ts")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-ts")  # rev 1 at T1
            time.sleep(1.1)  # ensure T2 > T1 at second resolution
            mid_iso = datetime.now(timezone.utc).isoformat()
            time.sleep(1.1)
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='new text' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            cur.close()
            open_brain.snapshot_thought(conn, tid, "insp-ts")  # rev 2 at T3

            # mid_iso falls between T1 and T3 → should return rev 1.
            result = time_travel.inspect_at_timestamp(
                conn, tid, "insp-ts", at_iso=mid_iso
            )
            assert result is not None
            assert result.revision == 1
            assert result.raw_text == "orig text"
            assert result.query_kind == "at-timestamp"
            assert result.query_value == mid_iso
        finally:
            _cleanup(conn, tid)

    def test_inspect_before_first_version_returns_none(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-ts-pre")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-ts-pre")
            # Query a timestamp WAY before the snapshot.
            past_iso = (
                datetime.now(timezone.utc) - timedelta(days=365)
            ).isoformat()
            result = time_travel.inspect_at_timestamp(
                conn, tid, "insp-ts-pre", at_iso=past_iso
            )
            assert result is None
        finally:
            _cleanup(conn, tid)

    def test_inspect_iso_with_z_suffix_parses(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-ts-z")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-ts-z")
            future_iso = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            result = time_travel.inspect_at_timestamp(
                conn, tid, "insp-ts-z", at_iso=future_iso
            )
            assert result is not None
            assert result.revision == 1
        finally:
            _cleanup(conn, tid)

    def test_inspect_invalid_iso_raises(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-ts-bad")
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="cannot parse"):
                time_travel.inspect_at_timestamp(
                    conn, tid, "insp-ts-bad", at_iso="not-a-date"
                )
        finally:
            _cleanup(conn, tid)

    def test_inspect_timestamp_cross_user_rejected(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-ts-csu-a")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-ts-csu-a")
            future_iso = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            with pytest.raises(RuntimeError, match="not in user scope"):
                time_travel.inspect_at_timestamp(
                    conn, tid, "insp-ts-csu-b", at_iso=future_iso
                )
        finally:
            _cleanup(conn, tid)


# ─── inspect_latest ─────────────────────────────────────────────────────────


class TestInspectLatest:
    def test_inspect_latest_returns_max_revision(self, conn):
        r = open_brain.capture(conn, text="x", user_id="insp-latest")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-latest")
            open_brain.snapshot_thought(conn, tid, "insp-latest")
            open_brain.snapshot_thought(conn, tid, "insp-latest")
            result = time_travel.inspect_latest(conn, tid, "insp-latest")
            assert result is not None
            assert result.revision == 3
            assert result.query_kind == "latest"
        finally:
            _cleanup(conn, tid)

    def test_inspect_latest_no_versions_returns_none(self, conn):
        r = open_brain.capture(
            conn, text="never snapshotted", user_id="insp-latest-empty"
        )
        tid = r["thought_id"]
        try:
            result = time_travel.inspect_latest(
                conn, tid, "insp-latest-empty"
            )
            assert result is None
        finally:
            _cleanup(conn, tid)


# ─── inspect after rollback (RB invariant exposed) ──────────────────────────


class TestInspectAfterRollback:
    def test_inspect_revision_unchanged_after_rollback(self, conn):
        """RB invariant (Lin/Li/Chen 2026 §12.1): rollback APPENDS new history,
        never rewrites. Querying the original revision still returns the
        original content; the rollback row is its own new revision.
        """
        r = open_brain.capture(conn, text="v1 content", user_id="insp-rb")
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-rb")  # rev 1
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='v2 content' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            cur.close()
            open_brain.snapshot_thought(conn, tid, "insp-rb")  # rev 2
            # Roll back to v1 → creates rev 3 with v1 content.
            open_brain.rollback_thought(conn, tid, "insp-rb", to_revision=1)

            # Revision 1 still inspectable as original.
            r1 = time_travel.inspect_at_revision(
                conn, tid, "insp-rb", revision=1
            )
            assert r1 is not None
            assert r1.raw_text == "v1 content"
            assert r1.prov_activity == "snapshot"

            # Revision 3 is the rollback row carrying v1 content.
            r3 = time_travel.inspect_at_revision(
                conn, tid, "insp-rb", revision=3
            )
            assert r3 is not None
            assert r3.raw_text == "v1 content"
            assert r3.prov_activity == "rollback"

            # Latest returns revision 3.
            latest = time_travel.inspect_latest(conn, tid, "insp-rb")
            assert latest is not None
            assert latest.revision == 3
        finally:
            _cleanup(conn, tid)


# ─── CLI surface ────────────────────────────────────────────────────────────


class TestInspectCli:
    def test_help_includes_inspect_flags(self):
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0
        for flag in ("--inspect", "--at", "--at-revision"):
            assert flag in result.stdout, f"{flag} not in --help"

    def test_inspect_json_output_schema(self, conn):
        r = open_brain.capture(
            conn, text="cli inspect test", user_id="insp-cli"
        )
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-cli")
            result = subprocess.run(
                [
                    "python3",
                    "scripts/open_brain.py",
                    "--inspect",
                    tid,
                    "--at-revision",
                    "1",
                    "--json",
                ],
                capture_output=True,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                env={**os.environ, "USER": "insp-cli"},
            )
            assert result.returncode == 0, (
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            data = json.loads(result.stdout)
            assert data["thought_id"] == tid
            assert data["revision"] == 1
            assert "raw_text" in data
            assert data["query_kind"] == "at-revision"
            assert data["query_value"] == "1"
            assert data["prov_activity"] == "snapshot"
        finally:
            _cleanup(conn, tid)

    def test_inspect_cli_rejects_mutually_exclusive_at_and_revision(self, conn):
        r = open_brain.capture(
            conn, text="mux test", user_id="insp-cli-mux"
        )
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-cli-mux")
            result = subprocess.run(
                [
                    "python3",
                    "scripts/open_brain.py",
                    "--inspect",
                    tid,
                    "--at",
                    "2026-05-21T00:00:00Z",
                    "--at-revision",
                    "1",
                ],
                capture_output=True,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                env={**os.environ, "USER": "insp-cli-mux"},
            )
            assert result.returncode != 0
            # Either stderr or stdout (when --json) must mention mutual exclusivity.
            combined = (result.stderr + result.stdout).lower()
            assert "mutually exclusive" in combined
        finally:
            _cleanup(conn, tid)

    def test_inspect_cli_missing_version_json(self, conn):
        r = open_brain.capture(
            conn, text="missing-rev test", user_id="insp-cli-miss"
        )
        tid = r["thought_id"]
        try:
            open_brain.snapshot_thought(conn, tid, "insp-cli-miss")
            result = subprocess.run(
                [
                    "python3",
                    "scripts/open_brain.py",
                    "--inspect",
                    tid,
                    "--at-revision",
                    "99",
                    "--json",
                ],
                capture_output=True,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                env={**os.environ, "USER": "insp-cli-miss"},
            )
            assert result.returncode == 0, (
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            data = json.loads(result.stdout)
            assert data["thought_id"] == tid
            assert data["result"] is None
            assert "no version" in data["message"].lower()
        finally:
            _cleanup(conn, tid)
