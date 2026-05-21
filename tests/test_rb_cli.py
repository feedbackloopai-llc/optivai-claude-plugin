"""brain-W1-S5: RB CLI tests.

Verifies snapshot, list_versions, rollback (with the load-bearing
"rollback creates new history" invariant from Lin/Li/Chen 2026 §12.1),
and diff_versions (RFC 6902 JSON Patch).

Run: python3 -m pytest tests/test_rb_cli.py -v
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


def _setup_thought(conn, user_id, text):
    """Helper: capture a thought + return its tid."""
    r = open_brain.capture(conn, text=text, user_id=user_id)
    return r["thought_id"]


def _cleanup_thought(conn, tid):
    """Helper: delete a thought (cascades to versions via FK ON DELETE CASCADE)."""
    cur = conn.cursor()
    try:
        # Knowledge-graph artefacts (best-effort; tables may not exist on every env).
        try:
            cur.execute(
                "DELETE FROM brain.kg_edges WHERE source_thought_id = %s OR target_thought_id = %s",
                (tid, tid),
            )
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.kg_nodes WHERE thought_id = %s",
                (tid,),
            )
        except Exception:
            conn.rollback()
        cur.execute("DELETE FROM brain.thoughts WHERE thought_id = %s", (tid,))
        conn.commit()
    finally:
        cur.close()


# ─── Snapshot ─────────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_first_snapshot_yields_revision_1(self, conn):
        tid = _setup_thought(conn, "rb-snap-user", "v1 text")
        try:
            r = open_brain.snapshot_thought(conn, tid, "rb-snap-user")
            assert r["revision"] == 1
            assert r["version_id"] > 0
            assert r["thought_id"] == tid
        finally:
            _cleanup_thought(conn, tid)

    def test_second_snapshot_yields_revision_2(self, conn):
        tid = _setup_thought(conn, "rb-snap2-user", "v1 text")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-snap2-user")
            r2 = open_brain.snapshot_thought(conn, tid, "rb-snap2-user")
            assert r2["revision"] == 2
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_cross_user_rejected(self, conn):
        tid = _setup_thought(conn, "rb-snap-userA", "user A's thought")
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.snapshot_thought(conn, tid, "rb-snap-userB")
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_records_prov_at_version_level(self, conn):
        tid = _setup_thought(conn, "rb-prov-user", "test prov")
        try:
            open_brain.snapshot_thought(
                conn, tid, "rb-prov-user", prov_agent="custom-snap-agent"
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent, prov_activity FROM brain.thought_versions WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "custom-snap-agent"
            assert row[1] == "snapshot"
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_default_prov_activity_is_snapshot(self, conn):
        tid = _setup_thought(conn, "rb-default-prov-user", "default prov")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-default-prov-user")
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_activity, prov_agent FROM brain.thought_versions WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row[0] == "snapshot"
            # Default prov_agent derives from "manual" + user_id
            assert row[1] == "cli-user-rb-default-prov-user"
        finally:
            _cleanup_thought(conn, tid)

    def test_snapshot_records_diff_against_previous(self, conn):
        """After v1, modifying the thought + snapshotting v2 should record a
        non-empty diff_json on v2 (RFC 6902 patch from v1 → v2)."""
        tid = _setup_thought(conn, "rb-diff-snap-user", "original")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-diff-snap-user")
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='modified' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-diff-snap-user")

            cur.execute(
                "SELECT diff_json FROM brain.thought_versions "
                "WHERE thought_id=%s AND revision=2",
                (tid,),
            )
            diff_json = cur.fetchone()[0]
            cur.close()
            assert diff_json is not None
            assert isinstance(diff_json, list)
            # Should include a replace op on /raw_text
            replace_ops = [
                op for op in diff_json
                if op.get("op") == "replace" and op.get("path") == "/raw_text"
            ]
            assert len(replace_ops) == 1
            assert replace_ops[0]["value"] == "modified"
        finally:
            _cleanup_thought(conn, tid)


# ─── List Versions ────────────────────────────────────────────────────────────


class TestListVersions:
    def test_no_versions_yields_empty_list(self, conn):
        tid = _setup_thought(conn, "rb-list-empty", "no versions yet")
        try:
            assert open_brain.list_versions(conn, tid, "rb-list-empty") == []
        finally:
            _cleanup_thought(conn, tid)

    def test_three_snapshots_yields_three_versions(self, conn):
        tid = _setup_thought(conn, "rb-list3-user", "v1")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-list3-user")
            open_brain.snapshot_thought(conn, tid, "rb-list3-user")
            open_brain.snapshot_thought(conn, tid, "rb-list3-user")
            versions = open_brain.list_versions(conn, tid, "rb-list3-user")
            assert len(versions) == 3
            assert [v["revision"] for v in versions] == [1, 2, 3]
            # Each entry has the expected display fields
            for v in versions:
                assert "version_id" in v
                assert "raw_text" in v
                assert "prov_agent" in v
                assert "prov_activity" in v
                assert "created_at" in v
        finally:
            _cleanup_thought(conn, tid)

    def test_list_versions_cross_user_rejected(self, conn):
        tid = _setup_thought(conn, "rb-list-userA", "x")
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.list_versions(conn, tid, "rb-list-userB")
        finally:
            _cleanup_thought(conn, tid)


# ─── Rollback (THE load-bearing tests) ────────────────────────────────────────


class TestRollbackCreatesNewHistory:
    """The LOAD-BEARING invariant tests. Do not delete or weaken.

    Lin/Li/Chen 2026 §12.1 RB primitive: rollback APPENDS, never overwrites.
    """

    def test_rollback_creates_new_history_not_rewrite(self, conn):
        """v1,v2,v3 thought + rollback(v1) → v1,v2,v3,v4 where v4 has v1's content."""
        tid = _setup_thought(conn, "rb-invariant-user", "v1 content")
        try:
            # Snapshot revision 1 (matches v1 content)
            open_brain.snapshot_thought(conn, tid, "rb-invariant-user")
            # Modify thought + snapshot v2
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='v2 content' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-invariant-user")
            # Modify + snapshot v3
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='v3 content' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-invariant-user")

            # Rollback to revision 1
            r = open_brain.rollback_thought(
                conn, tid, "rb-invariant-user", to_revision=1
            )
            assert r["revision"] == 4
            assert r["rolled_back_to_revision"] == 1

            # The HISTORY: 4 rows total — revisions 1,2,3,4
            versions = open_brain.list_versions(conn, tid, "rb-invariant-user")
            assert len(versions) == 4, f"Expected 4 versions, got {len(versions)}"
            assert [v["revision"] for v in versions] == [1, 2, 3, 4]

            # v4 has v1's content + prov_activity='rollback' + parent_version = v1
            cur.execute(
                "SELECT raw_text, prov_activity, parent_version "
                "FROM brain.thought_versions WHERE thought_id=%s AND revision=4",
                (tid,),
            )
            row = cur.fetchone()
            assert row[0] == "v1 content", f"v4 raw_text={row[0]} (expected 'v1 content')"
            assert row[1] == "rollback", f"v4 prov_activity={row[1]} (expected 'rollback')"
            # parent_version is the version_id of revision 1
            cur.execute(
                "SELECT version_id FROM brain.thought_versions "
                "WHERE thought_id=%s AND revision=1",
                (tid,),
            )
            v1_version_id = cur.fetchone()[0]
            assert row[2] == v1_version_id, (
                f"v4 parent_version={row[2]} (expected v1 version_id {v1_version_id})"
            )

            # Revisions 2 and 3 STILL EXIST — rollback did NOT delete them
            cur.execute(
                "SELECT raw_text FROM brain.thought_versions "
                "WHERE thought_id=%s AND revision=2",
                (tid,),
            )
            assert cur.fetchone()[0] == "v2 content"
            cur.execute(
                "SELECT raw_text FROM brain.thought_versions "
                "WHERE thought_id=%s AND revision=3",
                (tid,),
            )
            assert cur.fetchone()[0] == "v3 content"

            # The LIVE brain.thoughts row also reflects v1 content
            cur.execute(
                "SELECT raw_text FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == "v1 content"
            cur.close()
        finally:
            _cleanup_thought(conn, tid)

    def test_rollback_to_nonexistent_revision_raises(self, conn):
        tid = _setup_thought(conn, "rb-rev-bad-user", "v1")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-rev-bad-user")
            with pytest.raises(RuntimeError, match="revision .* not found"):
                open_brain.rollback_thought(
                    conn, tid, "rb-rev-bad-user", to_revision=999
                )
        finally:
            _cleanup_thought(conn, tid)

    def test_rollback_cross_user_rejected(self, conn):
        tid = _setup_thought(conn, "rb-rb-userA", "v1")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-rb-userA")
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.rollback_thought(
                    conn, tid, "rb-rb-userB", to_revision=1
                )
        finally:
            _cleanup_thought(conn, tid)

    def test_rollback_updates_live_thought_row(self, conn):
        """After rollback, brain.thoughts.raw_text matches the rolled-back revision."""
        tid = _setup_thought(conn, "rb-live-update-user", "v1 live")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-live-update-user")
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='v2 live' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-live-update-user")

            open_brain.rollback_thought(
                conn, tid, "rb-live-update-user", to_revision=1
            )

            cur.execute(
                "SELECT raw_text FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == "v1 live"
            cur.close()
        finally:
            _cleanup_thought(conn, tid)


# ─── Diff ─────────────────────────────────────────────────────────────────────


class TestDiff:
    def test_diff_returns_rfc6902_patch(self, conn):
        tid = _setup_thought(conn, "rb-diff-user", "original text")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-diff-user")  # v1
            cur = conn.cursor()
            cur.execute(
                "UPDATE brain.thoughts SET raw_text='modified text' WHERE thought_id=%s",
                (tid,),
            )
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-diff-user")  # v2

            patch = open_brain.diff_versions(
                conn, tid, "rb-diff-user", revision_a=1, revision_b=2
            )
            # Patch is a list of RFC 6902 ops
            assert isinstance(patch, list)
            assert len(patch) >= 1
            # Should contain a replace op on /raw_text
            replace_ops = [
                op for op in patch
                if op.get("op") == "replace" and op.get("path") == "/raw_text"
            ]
            assert len(replace_ops) == 1
            assert replace_ops[0]["value"] == "modified text"
            cur.close()
        finally:
            _cleanup_thought(conn, tid)

    def test_diff_missing_revision_raises(self, conn):
        tid = _setup_thought(conn, "rb-diff-bad-user", "x")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-diff-bad-user")
            with pytest.raises(RuntimeError, match="not found"):
                open_brain.diff_versions(
                    conn, tid, "rb-diff-bad-user",
                    revision_a=1, revision_b=999,
                )
        finally:
            _cleanup_thought(conn, tid)

    def test_diff_cross_user_rejected(self, conn):
        tid = _setup_thought(conn, "rb-diff-userA", "x")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-diff-userA")
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.diff_versions(
                    conn, tid, "rb-diff-userB",
                    revision_a=1, revision_b=1,
                )
        finally:
            _cleanup_thought(conn, tid)


# ─── CLI flags ────────────────────────────────────────────────────────────────


class TestCliFlags:
    def test_help_includes_rb_flags(self):
        import subprocess
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0, f"--help exited {result.returncode}: {result.stderr}"
        for flag in (
            "--snapshot",
            "--versions",
            "--rollback",
            "--diff",
            "--to-revision",
            "--from-revision",
        ):
            assert flag in result.stdout, f"{flag} not in --help output"


# ─── S6 corpus expansion ──────────────────────────────────────────────────────
# Gap items the S5 review noted: snapshot idempotency-with-no-changes semantics,
# version_id stability across rollback, monotonic revision invariant including
# post-rollback, and diff order-invariance.


class TestSnapshotIdempotencySemantics:
    """Snapshotting twice with no edits between creates 2 distinct revisions
    (NOT a no-op). Each snapshot is a distinct PROV event."""

    def test_double_snapshot_no_edit_creates_two_revisions(self, conn):
        tid = _setup_thought(conn, "rb-s6-double-snap", "unchanging text")
        try:
            r1 = open_brain.snapshot_thought(conn, tid, "rb-s6-double-snap")
            r2 = open_brain.snapshot_thought(conn, tid, "rb-s6-double-snap")
            assert r1["revision"] == 1
            assert r2["revision"] == 2
            assert r1["version_id"] != r2["version_id"]
            # diff_json of v2 (vs v1, no edit) should be empty patch
            cur = conn.cursor()
            cur.execute(
                "SELECT diff_json FROM brain.thought_versions WHERE version_id=%s",
                (r2["version_id"],),
            )
            row = cur.fetchone()
            diff = row[0] if row else None
            assert diff is None or diff == [], (
                f"Expected empty diff (no changes between v1 and v2), got {diff}"
            )
        finally:
            _cleanup_thought(conn, tid)


class TestVersionIdStabilityAcrossRollback:
    """The version_id of any revision (including the rollback TARGET) is
    immutable after rollback. RB never modifies existing thought_versions rows."""

    def test_rollback_does_not_modify_target_version_row(self, conn):
        tid = _setup_thought(conn, "rb-s6-stability", "v1 content")
        try:
            r1 = open_brain.snapshot_thought(conn, tid, "rb-s6-stability")
            cur = conn.cursor()
            cur.execute("UPDATE brain.thoughts SET raw_text='v2 content' WHERE thought_id=%s", (tid,))
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-s6-stability")
            # Capture v1's row state pre-rollback
            cur.execute(
                "SELECT version_id, revision, raw_text, prov_activity, created_at "
                "FROM brain.thought_versions WHERE version_id=%s",
                (r1["version_id"],),
            )
            v1_pre = cur.fetchone()
            # Roll back to v1
            open_brain.rollback_thought(conn, tid, "rb-s6-stability", to_revision=1)
            # Verify v1's row is UNCHANGED post-rollback
            cur.execute(
                "SELECT version_id, revision, raw_text, prov_activity, created_at "
                "FROM brain.thought_versions WHERE version_id=%s",
                (r1["version_id"],),
            )
            v1_post = cur.fetchone()
            assert v1_pre == v1_post, (
                f"Rollback modified target revision row.\n"
                f"  pre:  {v1_pre}\n  post: {v1_post}"
            )
        finally:
            _cleanup_thought(conn, tid)

    def test_rollback_target_remains_diffable(self, conn):
        """After rollback, diff(target, rollback_row) is empty (they match)."""
        tid = _setup_thought(conn, "rb-s6-target-ref", "v1")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-s6-target-ref")
            cur = conn.cursor()
            cur.execute("UPDATE brain.thoughts SET raw_text='v2' WHERE thought_id=%s", (tid,))
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-s6-target-ref")
            open_brain.rollback_thought(conn, tid, "rb-s6-target-ref", to_revision=1)
            # Diff v1 vs the new rollback row (revision 3) — should be empty
            patch = open_brain.diff_versions(conn, tid, "rb-s6-target-ref",
                                              revision_a=1, revision_b=3)
            assert patch == [], f"Rollback v3 should mirror v1; diff={patch}"
        finally:
            _cleanup_thought(conn, tid)


class TestMonotonicRevisionInvariant:
    """Revisions are strictly monotonic per thought_id including after multiple
    rollbacks. The UNIQUE(thought_id, revision) constraint plus next_revision =
    max + 1 formula guarantee this."""

    def test_revisions_strictly_monotonic_through_rollback(self, conn):
        tid = _setup_thought(conn, "rb-s6-monotone", "v1")
        try:
            # 3 snapshots → revisions [1, 2, 3]. Each loop edits the live thought.
            for i in range(2, 5):
                open_brain.snapshot_thought(conn, tid, "rb-s6-monotone")
                cur = conn.cursor()
                cur.execute(
                    "UPDATE brain.thoughts SET raw_text=%s WHERE thought_id=%s",
                    (f"v{i}", tid),
                )
                conn.commit()
            # Roll back to v2 → appends rev 4
            r = open_brain.rollback_thought(conn, tid, "rb-s6-monotone", to_revision=2)
            assert r["revision"] == 4
            # Snapshot → rev 5
            r2 = open_brain.snapshot_thought(conn, tid, "rb-s6-monotone")
            assert r2["revision"] == 5
            # Roll back to v3 → rev 6
            r3 = open_brain.rollback_thought(conn, tid, "rb-s6-monotone", to_revision=3)
            assert r3["revision"] == 6
            # Verify all 6 in sequence (no gaps; strictly monotonic)
            versions = open_brain.list_versions(conn, tid, "rb-s6-monotone")
            assert [v["revision"] for v in versions] == [1, 2, 3, 4, 5, 6]
        finally:
            _cleanup_thought(conn, tid)


class TestDiffDirectionSemantics:
    """diff_versions is direction-sensitive: A→B and B→A produce reverse patches.
    A→B reads as 'how to transform A into B'. B→A reads as 'how to transform B
    back to A'. Both are valid; useful for forward vs. undo direction."""

    def test_diff_forward_direction_replaces_to_new_value(self, conn):
        tid = _setup_thought(conn, "rb-s6-revdiff-fwd", "original")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-s6-revdiff-fwd")
            cur = conn.cursor()
            cur.execute("UPDATE brain.thoughts SET raw_text='modified' WHERE thought_id=%s", (tid,))
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-s6-revdiff-fwd")
            patch = open_brain.diff_versions(conn, tid, "rb-s6-revdiff-fwd",
                                              revision_a=1, revision_b=2)
            replace_ops = [op for op in patch if op.get("op") == "replace" and op.get("path") == "/raw_text"]
            assert len(replace_ops) == 1
            assert replace_ops[0]["value"] == "modified"
        finally:
            _cleanup_thought(conn, tid)

    def test_diff_reverse_direction_replaces_to_old_value(self, conn):
        """Calling diff with revision_a=2, revision_b=1 yields the REVERSE patch
        (undo direction). The replace value is the OLD content, not the new."""
        tid = _setup_thought(conn, "rb-s6-revdiff-rev", "original")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-s6-revdiff-rev")
            cur = conn.cursor()
            cur.execute("UPDATE brain.thoughts SET raw_text='modified' WHERE thought_id=%s", (tid,))
            conn.commit()
            open_brain.snapshot_thought(conn, tid, "rb-s6-revdiff-rev")
            patch = open_brain.diff_versions(conn, tid, "rb-s6-revdiff-rev",
                                              revision_a=2, revision_b=1)
            replace_ops = [op for op in patch if op.get("op") == "replace" and op.get("path") == "/raw_text"]
            assert len(replace_ops) == 1
            assert replace_ops[0]["value"] == "original"  # undo direction
        finally:
            _cleanup_thought(conn, tid)

    def test_diff_same_revision_returns_empty(self, conn):
        tid = _setup_thought(conn, "rb-s6-samerev", "x")
        try:
            open_brain.snapshot_thought(conn, tid, "rb-s6-samerev")
            patch = open_brain.diff_versions(conn, tid, "rb-s6-samerev",
                                              revision_a=1, revision_b=1)
            assert patch == []
        finally:
            _cleanup_thought(conn, tid)
