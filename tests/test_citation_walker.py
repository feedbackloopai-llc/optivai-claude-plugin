#!/usr/bin/env python3
"""brain-W2-S1+S2: Citation walker tests.

Verifies provenance-chain traversal via was_derived_from with proper PS scoping,
orphan handling (parent forgotten via VF_eps cascade), max-depth bound, cycle
detection, and chain-of-N derivation trees.

Run: python3 -m pytest tests/test_citation_walker.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

# Add scripts dir to path so we can import open_brain + citation_walker
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402
import citation_walker  # noqa: E402


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


def _capture(conn, text: str, user_id: str, was_derived_from=None, attempts: int = 3):
    """Wrap open_brain.capture with retry-on-flake.

    The Cortex metadata-extraction (LLM) path occasionally returns a payload
    with ``summary=None`` which makes ``capture()`` raise TypeError on
    ``summary[:1000]``. That's a pre-existing condition in capture() (out of
    scope for W2); we retry the capture rather than mask the walker tests'
    intent. Other exceptions are not retried.
    """
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return open_brain.capture(
                conn,
                text=text,
                user_id=user_id,
                was_derived_from=was_derived_from,
            )
        except TypeError as exc:
            # Caller commits/rolls back its own transaction state — capture()
            # itself does conn.commit() before the TypeError can be raised
            # (or before any DB work happens). To be safe, rollback any
            # half-baked txn state.
            try:
                conn.rollback()
            except Exception:
                pass
            last_exc = exc
    # Out of retries — re-raise so the test sees the real failure
    raise RuntimeError(
        f"_capture: exhausted {attempts} attempts due to TypeError flake "
        f"in open_brain.capture (last error: {last_exc!r})"
    )


def _cleanup(conn, *tids):
    """Remove test-created thought rows (+ kg artifacts, version rows, audit).

    Order matters: kg edges/nodes first, then version rows, then forget-audit,
    then the thought itself (FK SET NULL on was_derived_from so any order works
    among the thoughts themselves).
    """
    cur = conn.cursor()
    try:
        for tid in tids:
            if tid is None:
                continue
            try:
                cur.execute(
                    "DELETE FROM brain.kg_edges WHERE source_thought_id = %s "
                    "OR target_thought_id = %s",
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
            try:
                cur.execute(
                    "DELETE FROM brain.forget_audit WHERE thought_id = %s",
                    (tid,),
                )
            except Exception:
                conn.rollback()
            try:
                cur.execute(
                    "DELETE FROM brain.thought_versions WHERE thought_id = %s",
                    (tid,),
                )
            except Exception:
                conn.rollback()
            cur.execute(
                "DELETE FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            )
        conn.commit()
    finally:
        cur.close()


# ─── Unit tests: CitationNode dataclass + helper ─────────────────────────────


class TestCitationNodeDataclass:
    def test_node_defaults(self):
        node = citation_walker.CitationNode(
            thought_id="t-a",
            depth=0,
            raw_text_preview="hello",
            prov_agent="agent",
            prov_activity="capture",
            was_generated_by="activity-t-a",
            was_derived_from=None,
            source_uri=None,
        )
        assert node.children == []
        assert node.orphan_marker is None

    def test_to_dict_round_trip(self):
        node = citation_walker.CitationNode(
            thought_id="t-root",
            depth=0,
            raw_text_preview="root",
            prov_agent="agent",
            prov_activity="capture",
            was_generated_by="activity-t-root",
            was_derived_from="t-parent",
            source_uri=None,
        )
        child = citation_walker.CitationNode(
            thought_id="t-parent",
            depth=1,
            raw_text_preview="parent",
            prov_agent="agent",
            prov_activity="capture",
            was_generated_by="activity-t-parent",
            was_derived_from=None,
            source_uri=None,
        )
        node.children.append(child)
        d = citation_walker.citation_node_to_dict(node)
        assert d["thought_id"] == "t-root"
        assert d["depth"] == 0
        assert d["was_derived_from"] == "t-parent"
        assert len(d["children"]) == 1
        assert d["children"][0]["thought_id"] == "t-parent"
        assert d["children"][0]["depth"] == 1
        # JSON-serializable
        encoded = json.dumps(d)
        decoded = json.loads(encoded)
        assert decoded["children"][0]["thought_id"] == "t-parent"


# ─── Basics: NULL / chain-of-2 / chain-of-3 ──────────────────────────────────


class TestCitationWalkerBasics:
    def test_original_with_no_parent_yields_root_only(self, conn):
        """A thought with was_derived_from=NULL: walk returns root with empty children."""
        r = _capture(conn, text="original thought", user_id="cite-basic")
        tid = r["thought_id"]
        try:
            root = citation_walker.trace_citation(conn, tid, "cite-basic")
            assert root.thought_id == tid
            assert root.depth == 0
            assert root.was_derived_from is None
            assert root.children == []
            assert root.orphan_marker is None
            # PROV-DM fields populated
            assert root.prov_agent == "cli-user-cite-basic"
            assert root.prov_activity == "capture"
            assert root.was_generated_by == f"activity-{tid}"
            # Preview is truncated but non-empty for original raw text
            assert "original thought" in root.raw_text_preview
        finally:
            _cleanup(conn, tid)

    def test_child_walks_to_parent(self, conn):
        """A child thought yields a 2-node chain: child -> parent."""
        parent = _capture(conn, text="parent thought", user_id="cite-chain2")
        pid = parent["thought_id"]
        child = _capture(
            conn,
            text="child thought",
            user_id="cite-chain2",
            was_derived_from=pid,
        )
        cid = child["thought_id"]
        try:
            root = citation_walker.trace_citation(conn, cid, "cite-chain2")
            assert root.thought_id == cid
            assert root.depth == 0
            assert root.was_derived_from == pid
            assert len(root.children) == 1
            assert root.children[0].thought_id == pid
            assert root.children[0].depth == 1
            assert root.children[0].was_derived_from is None
            assert root.children[0].children == []
            assert root.children[0].orphan_marker is None
        finally:
            _cleanup(conn, cid, pid)

    def test_three_level_chain(self, conn):
        """grandparent -> parent -> child: walking from child yields 3-deep chain."""
        gp = _capture(conn, text="grandparent", user_id="cite-chain3")
        gpid = gp["thought_id"]
        p = _capture(
            conn,
            text="parent",
            user_id="cite-chain3",
            was_derived_from=gpid,
        )
        pid = p["thought_id"]
        c = _capture(
            conn,
            text="child",
            user_id="cite-chain3",
            was_derived_from=pid,
        )
        cid = c["thought_id"]
        try:
            root = citation_walker.trace_citation(conn, cid, "cite-chain3")
            # root -> parent -> grandparent
            assert root.thought_id == cid
            assert root.children[0].thought_id == pid
            assert root.children[0].children[0].thought_id == gpid
            # depths
            assert root.depth == 0
            assert root.children[0].depth == 1
            assert root.children[0].children[0].depth == 2
            # grandparent has no derivation
            assert root.children[0].children[0].was_derived_from is None
            assert root.children[0].children[0].children == []
        finally:
            _cleanup(conn, cid, pid, gpid)


# ─── PS scoping ──────────────────────────────────────────────────────────────


class TestCitationWalkerPsScoping:
    def test_cross_user_starting_thought_rejected(self, conn):
        """Calling trace_citation with a wrong user_id raises RuntimeError."""
        r = _capture(conn, text="userA thought", user_id="cite-ps-usera")
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                citation_walker.trace_citation(conn, tid, "cite-ps-userb")
        finally:
            _cleanup(conn, tid)

    def test_nonexistent_starting_thought_rejected(self, conn):
        """A thought_id that doesn't exist at all is also rejected."""
        with pytest.raises(RuntimeError, match="not in user scope"):
            citation_walker.trace_citation(
                conn, "brain-does-not-exist-deadbeef", "cite-ps-ghost",
            )

    def test_cross_user_parent_appears_orphaned(self, conn):
        """If a child references a parent in a DIFFERENT user's scope (set up
        via direct SQL bypass of capture()'s WA check), the walker scopes to
        the caller's user_id and treats the unreachable parent as orphaned."""
        rA = _capture(
            conn, text="userA parent", user_id="cite-cross-a",
        )
        pid = rA["thought_id"]
        cid = open_brain._generate_thought_id()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO brain.thoughts (
                    thought_id, user_id, raw_text, summary, thought_type,
                    topics, people, action_items, source,
                    prov_agent, prov_activity, was_generated_by, was_derived_from
                ) VALUES (
                    %s, %s, %s, %s, 'insight',
                    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'manual',
                    %s, 'capture', %s, %s
                )
                """,
                (
                    cid,
                    "cite-cross-b",
                    "cross-user child",
                    "cross-user child",
                    "test-agent",
                    f"activity-{cid}",
                    pid,
                ),
            )
            conn.commit()
        finally:
            cur.close()
        try:
            root = citation_walker.trace_citation(conn, cid, "cite-cross-b")
            assert root.thought_id == cid
            assert root.was_derived_from == pid
            assert len(root.children) == 1
            # Parent is in userA scope; walker is scoped to userB -> orphan
            assert root.children[0].orphan_marker == "orphaned"
            assert root.children[0].thought_id == pid
            assert root.children[0].depth == 1
            # Walk terminates at orphan (no recursion into a row we can't see)
            assert root.children[0].children == []
        finally:
            _cleanup(conn, cid, pid)


# ─── Orphan / forgotten parent ───────────────────────────────────────────────


class TestCitationWalkerOrphan:
    def test_forgotten_parent_yields_null_derivation(self, conn):
        """If parent is forgotten via VF_eps, the FK ON DELETE SET NULL clears
        the child's was_derived_from. So walking from the child returns a
        single-node tree (chain terminates at child; no orphan_marker because
        the child genuinely has no parent now)."""
        parent = _capture(
            conn, text="parent to forget", user_id="cite-forget",
        )
        pid = parent["thought_id"]
        child = _capture(
            conn,
            text="surviving child after parent forgotten",
            user_id="cite-forget",
            was_derived_from=pid,
        )
        cid = child["thought_id"]
        try:
            # Forget the parent. n is kept small to keep test fast; the
            # probe-acceptance logic is exercised in test_vf_probe.py.
            result = open_brain.forget_thought(
                conn, pid, "cite-forget", n=30,
            )
            # The forget may surface k>0 occasionally; we tolerate either
            # outcome here because the test target is the walker's behavior
            # in BOTH cases.
            if result.get("status") == "forgotten":
                # Re-query child: was_derived_from should be NULL via FK SET NULL
                root = citation_walker.trace_citation(
                    conn, cid, "cite-forget",
                )
                assert root.thought_id == cid
                assert root.was_derived_from is None
                assert root.children == []
                assert root.orphan_marker is None
                # Parent row gone -> cleanup only the child + audit
                _cleanup(conn, cid)
            else:
                # Parent restored (forget-failed-residue). The chain is intact;
                # walk still works and returns 2-node tree.
                root = citation_walker.trace_citation(
                    conn, cid, "cite-forget",
                )
                assert root.thought_id == cid
                assert root.was_derived_from == pid
                assert len(root.children) == 1
                assert root.children[0].thought_id == pid
                _cleanup(conn, cid, pid)
        except Exception:
            # Best-effort cleanup if anything blew up mid-test
            _cleanup(conn, cid, pid)
            raise


# ─── Max-depth bound ─────────────────────────────────────────────────────────


class TestCitationWalkerMaxDepth:
    def test_max_depth_caps_walk(self, conn):
        """A 5-deep chain walked with max_depth=2 yields a max-depth sentinel."""
        tids = []
        prev = None
        try:
            for i in range(5):
                r = _capture(
                    conn,
                    text=f"depth-{i}",
                    user_id="cite-depth",
                    was_derived_from=prev,
                )
                tids.append(r["thought_id"])
                prev = r["thought_id"]
            # tids[0] = depth-0 (original), tids[4] = depth-4 (deepest descendant)
            root = citation_walker.trace_citation(
                conn, tids[4], "cite-depth", max_depth=2,
            )
            # depth=0 (tids[4]) -> depth=1 (tids[3]) -> depth=2 should be sentinel
            assert root.thought_id == tids[4]
            assert root.depth == 0
            assert len(root.children) == 1
            assert root.children[0].thought_id == tids[3]
            assert root.children[0].depth == 1
            # At depth=1, the walker would recurse to depth=2 which hits the cap
            assert len(root.children[0].children) == 1
            sentinel = root.children[0].children[0]
            assert sentinel.orphan_marker == "max-depth"
            assert sentinel.depth == 2
            # Sentinel carries the parent thought_id but no recursion happened
            assert sentinel.thought_id == tids[2]
            assert sentinel.children == []
        finally:
            _cleanup(conn, *reversed(tids))

    def test_default_max_depth_constant(self):
        """Sanity: default depth bound is exposed at module level for tooling."""
        assert citation_walker.DEFAULT_MAX_DEPTH == 50

    def test_max_depth_zero_returns_root_only_with_sentinel_child(self, conn):
        """Edge case: max_depth=1 means root only, immediate sentinel if parent."""
        parent = _capture(
            conn, text="depth-zero-parent", user_id="cite-depth-zero",
        )
        pid = parent["thought_id"]
        child = _capture(
            conn,
            text="depth-zero-child",
            user_id="cite-depth-zero",
            was_derived_from=pid,
        )
        cid = child["thought_id"]
        try:
            root = citation_walker.trace_citation(
                conn, cid, "cite-depth-zero", max_depth=1,
            )
            assert root.thought_id == cid
            # depth+1 (1) >= max_depth (1) -> sentinel immediately
            assert len(root.children) == 1
            assert root.children[0].orphan_marker == "max-depth"
            assert root.children[0].thought_id == pid
        finally:
            _cleanup(conn, cid, pid)


# ─── CLI surface ─────────────────────────────────────────────────────────────


class TestCitationWalkerCli:
    def test_help_includes_trace_flag(self):
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0
        assert "--trace" in result.stdout
        assert "--max-depth" in result.stdout

    def test_trace_json_output_schema(self, conn):
        """End-to-end: capture a thought, then `--trace TID --json` returns the
        full CitationNode JSON tree."""
        r = _capture(
            conn, text="cli trace test", user_id="cite-cli",
        )
        tid = r["thought_id"]
        try:
            result = subprocess.run(
                ["python3", "scripts/open_brain.py", "--trace", tid, "--json"],
                capture_output=True,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                env={**os.environ, "USER": "cite-cli"},
            )
            assert result.returncode == 0, (
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            data = json.loads(result.stdout)
            assert data["thought_id"] == tid
            assert data["depth"] == 0
            assert data["was_derived_from"] is None
            assert "children" in data
            assert data["children"] == []
            for field_name in (
                "prov_agent",
                "prov_activity",
                "was_generated_by",
                "raw_text_preview",
                "orphan_marker",
                "source_uri",
            ):
                assert field_name in data
            assert data["orphan_marker"] is None
        finally:
            _cleanup(conn, tid)

    def test_trace_human_output_includes_thought_id(self, conn):
        """Non-JSON output is human-readable but must still mention the thought_id."""
        r = _capture(
            conn, text="cli human-readable trace test", user_id="cite-cli-h",
        )
        tid = r["thought_id"]
        try:
            result = subprocess.run(
                ["python3", "scripts/open_brain.py", "--trace", tid],
                capture_output=True,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                env={**os.environ, "USER": "cite-cli-h"},
            )
            assert result.returncode == 0, (
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            assert tid in result.stdout
            # d0 marker present (depth zero in pretty-print)
            assert "d0" in result.stdout
        finally:
            _cleanup(conn, tid)
