#!/usr/bin/env python3
"""fblai-mt4z0 — T3.1 Pi dispatcher ops tests.

Verifies that _run_from_pi() correctly dispatches the new ops added in T3.1:
  - add_link: routes source_id/target_id/link_type to add_link()
  - show_links: routes atom_id to show_links()
  - register_skill: routes name/description/from_patterns to register_skill()
  - query_unresolved_findings: routes to query_unresolved_findings()
  - query_orphan_links: routes to query_orphan_links()
  - capture with links list: validates specs and calls add_link per entry
  - capture with stv passthrough: already covered in test_nal_stv.py; this
    file adds capture-with-links coverage only.

All tests mock the DB connection and the underlying functions so no live DB
is required. They call _run_from_pi() via stdin monkeypatching exactly as
test_bridge_auth.py does.

Run: python3 -m pytest scripts/tests/test_pi_bridge_t31.py -v
"""
import io
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Helper ───────────────────────────────────────────────────────────────────


def _run_pi(envelope: dict, patches: dict = None) -> dict:
    """Feed *envelope* through _run_from_pi() with a mocked DB connection.

    *patches* maps open_brain attribute names to mock side_effect or
    return_value callables.  Returns the parsed JSON output dict.
    """
    raw = json.dumps(envelope)
    captured = io.StringIO()
    fake_conn = mock.MagicMock()

    ctx_managers = [
        mock.patch("open_brain._connect", return_value=fake_conn),
        mock.patch("sys.stdin", io.StringIO(raw)),
        mock.patch("sys.stdout", captured),
    ]
    if patches:
        for attr, value in patches.items():
            if callable(value):
                ctx_managers.append(mock.patch.object(open_brain, attr, side_effect=value))
            else:
                ctx_managers.append(mock.patch.object(open_brain, attr, return_value=value))

    with mock.patch("open_brain._connect", return_value=fake_conn), \
         mock.patch("sys.stdin", io.StringIO(raw)), \
         mock.patch("sys.stdout", captured):
        if patches:
            patch_ctxs = []
            for attr, value in patches.items():
                if callable(value):
                    patch_ctxs.append(mock.patch.object(open_brain, attr, side_effect=value))
                else:
                    patch_ctxs.append(mock.patch.object(open_brain, attr, return_value=value))
            # Apply all patches
            active = []
            for p in patch_ctxs:
                active.append(p.__enter__())
            try:
                open_brain._run_from_pi()
            finally:
                for p in patch_ctxs:
                    p.__exit__(None, None, None)
        else:
            open_brain._run_from_pi()

    out = captured.getvalue().strip()
    assert out, "Pi bridge produced no output"
    return json.loads(out)


# ─── add_link ─────────────────────────────────────────────────────────────────


class TestPiBridgeAddLink:
    """_run_from_pi 'add_link' op routes to add_link()."""

    def test_add_link_dispatches_correctly(self):
        """add_link op calls add_link() with source_id/target_id/link_type."""
        calls = []

        def spy_add_link(conn, source_id, target_id, link_type, user_id, **kwargs):
            calls.append({
                "source_id": source_id,
                "target_id": target_id,
                "link_type": link_type,
                "user_id": user_id,
            })
            return {"link_id": 42, "source_id": source_id, "target_id": target_id,
                    "link_type": link_type, "created": True}

        envelope = {
            "op": "add_link",
            "source_id": "brain-test-src",
            "target_id": "brain-test-tgt",
            "link_type": "verifies",
        }
        result = _run_pi(envelope, {"add_link": spy_add_link})
        assert "error" not in result, f"Unexpected error: {result}"
        assert result["link_id"] == 42
        assert len(calls) == 1
        assert calls[0]["source_id"] == "brain-test-src"
        assert calls[0]["target_id"] == "brain-test-tgt"
        assert calls[0]["link_type"] == "verifies"
        # user_id must be OS-derived, never a spoofed value
        os_user = (os.environ.get("USER") or os.environ.get("USERNAME") or "unknown").lower()
        assert calls[0]["user_id"] == os_user

    def test_add_link_missing_source_id_returns_error(self):
        """add_link op with missing source_id returns error JSON."""
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "add_link", "target_id": "t", "link_type": "verifies"
             }))), \
             mock.patch("sys.stdout", io.StringIO()) as fake_out:
            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                open_brain._run_from_pi()
        out = captured.getvalue().strip()
        data = json.loads(out)
        assert "error" in data

    def test_add_link_missing_link_type_returns_error(self):
        """add_link op with missing link_type returns error JSON."""
        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "add_link", "source_id": "s", "target_id": "t"
             }))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data

    def test_add_link_ValueError_returns_error(self):
        """add_link op surfacing ValueError (e.g. invalid link_type) returns error JSON."""
        def raise_value_error(*args, **kwargs):
            raise ValueError("unknown link_type 'bogus'; allowed: ...")

        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch.object(open_brain, "add_link", side_effect=raise_value_error), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "add_link",
                 "source_id": "brain-s",
                 "target_id": "brain-t",
                 "link_type": "bogus",
             }))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data
        assert "bogus" in data["error"]


# ─── show_links ───────────────────────────────────────────────────────────────


class TestPiBridgeShowLinks:
    """_run_from_pi 'show_links' op routes to show_links()."""

    def test_show_links_dispatches_correctly(self):
        """show_links op calls show_links() with atom_id."""
        calls = []

        def spy_show_links(conn, atom_id, user_id):
            calls.append({"atom_id": atom_id, "user_id": user_id})
            return {"atom_id": atom_id, "outgoing": [], "incoming": []}

        result = _run_pi(
            {"op": "show_links", "atom_id": "brain-test-123"},
            {"show_links": spy_show_links},
        )
        assert "error" not in result
        assert result["atom_id"] == "brain-test-123"
        assert len(calls) == 1
        assert calls[0]["atom_id"] == "brain-test-123"

    def test_show_links_missing_atom_id_returns_error(self):
        """show_links op with missing atom_id returns error JSON."""
        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({"op": "show_links"}))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data


# ─── register_skill ───────────────────────────────────────────────────────────


class TestPiBridgeRegisterSkill:
    """_run_from_pi 'register_skill' op routes to register_skill()."""

    def test_register_skill_dispatches_correctly(self):
        """register_skill op calls register_skill() with name/description."""
        calls = []

        def spy_register_skill(conn, name, description, user_id, **kwargs):
            calls.append({"name": name, "description": description, "user_id": user_id})
            return {"skill_id": "brain-skill-abc", "name": name,
                    "promoted_weight": 2.0, "linked_from_patterns": []}

        result = _run_pi(
            {
                "op": "register_skill",
                "name": "test-skill",
                "description": "A skill for pi-bridge testing purposes only.",
            },
            {"register_skill": spy_register_skill},
        )
        assert "error" not in result, f"Unexpected error: {result}"
        assert result["skill_id"] == "brain-skill-abc"
        assert len(calls) == 1
        assert calls[0]["name"] == "test-skill"
        os_user = (os.environ.get("USER") or os.environ.get("USERNAME") or "unknown").lower()
        assert calls[0]["user_id"] == os_user

    def test_register_skill_passes_from_patterns(self):
        """register_skill op forwards from_patterns list."""
        calls = []

        def spy_register_skill(conn, name, description, user_id, from_patterns=None, **kwargs):
            calls.append({"from_patterns": from_patterns})
            return {"skill_id": "brain-skill-xyz", "name": name,
                    "promoted_weight": 2.0, "linked_from_patterns": from_patterns or []}

        result = _run_pi(
            {
                "op": "register_skill",
                "name": "another-skill",
                "description": "Another skill for testing from_patterns forwarding.",
                "from_patterns": ["brain-123", "brain-456"],
            },
            {"register_skill": spy_register_skill},
        )
        assert "error" not in result
        assert len(calls) == 1
        assert calls[0]["from_patterns"] == ["brain-123", "brain-456"]

    def test_register_skill_missing_name_returns_error(self):
        """register_skill op with missing name returns error JSON."""
        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "register_skill", "description": "some description here"
             }))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data

    def test_register_skill_ValueError_propagated(self):
        """register_skill op surfacing ValueError returns error JSON."""
        def raise_ve(*args, **kwargs):
            raise ValueError("invalid skill name 'BAD_NAME'")

        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch.object(open_brain, "register_skill", side_effect=raise_ve), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "register_skill",
                 "name": "BAD_NAME",
                 "description": "A description that is long enough to pass validation.",
             }))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data
        assert "BAD_NAME" in data["error"]


# ─── query_unresolved_findings ────────────────────────────────────────────────


class TestPiBridgeQueryUnresolvedFindings:
    """_run_from_pi 'query_unresolved_findings' op routes correctly."""

    def test_query_unresolved_findings_dispatches(self):
        """query_unresolved_findings op calls query_unresolved_findings()."""
        calls = []

        def spy_quf(conn, user_id, limit=50):
            calls.append({"user_id": user_id, "limit": limit})
            return [{"thought_id": "brain-finding-1", "summary": "Test finding"}]

        result = _run_pi(
            {"op": "query_unresolved_findings", "limit": 25},
            {"query_unresolved_findings": spy_quf},
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["thought_id"] == "brain-finding-1"
        assert len(calls) == 1
        assert calls[0]["limit"] == 25
        os_user = (os.environ.get("USER") or os.environ.get("USERNAME") or "unknown").lower()
        assert calls[0]["user_id"] == os_user

    def test_query_unresolved_findings_default_limit(self):
        """query_unresolved_findings op uses default limit=50 when not provided."""
        calls = []

        def spy_quf(conn, user_id, limit=50):
            calls.append({"limit": limit})
            return []

        _run_pi({"op": "query_unresolved_findings"}, {"query_unresolved_findings": spy_quf})
        assert calls[0]["limit"] == 50


# ─── query_orphan_links ───────────────────────────────────────────────────────


class TestPiBridgeQueryOrphanLinks:
    """_run_from_pi 'query_orphan_links' op routes correctly."""

    def test_query_orphan_links_dispatches(self):
        """query_orphan_links op calls query_orphan_links()."""
        calls = []

        def spy_qol(conn, user_id, limit=100):
            calls.append({"user_id": user_id, "limit": limit})
            return [{"link_id": 7, "source_id": "brain-s", "target_id": "brain-dangling",
                     "link_type": "verifies", "created_at": "2026-01-01T00:00:00"}]

        result = _run_pi(
            {"op": "query_orphan_links", "limit": 10},
            {"query_orphan_links": spy_qol},
        )
        assert isinstance(result, list)
        assert result[0]["link_id"] == 7
        assert calls[0]["limit"] == 10

    def test_query_orphan_links_default_limit(self):
        """query_orphan_links op uses default limit=100 when not provided."""
        calls = []

        def spy_qol(conn, user_id, limit=100):
            calls.append({"limit": limit})
            return []

        _run_pi({"op": "query_orphan_links"}, {"query_orphan_links": spy_qol})
        assert calls[0]["limit"] == 100


# ─── capture with links passthrough ──────────────────────────────────────────


class TestPiBridgeCaptureWithLinks:
    """_run_from_pi 'capture' op + links list calls add_link after capture."""

    def test_capture_with_links_list_of_dicts(self):
        """capture op with links=[{target_id, link_type}] calls add_link per entry."""
        capture_calls = []
        link_calls = []

        def spy_capture(conn, text, user_id, **kwargs):
            capture_calls.append({"text": text, "user_id": user_id})
            return {
                "thought_id": "brain-new-atom",
                "summary": "test capture",
                "type": "insight",
                "topics": [],
                "people": [],
                "action_items": [],
            }

        def spy_add_link(conn, source_id, target_id, link_type, user_id, **kwargs):
            link_calls.append({
                "source_id": source_id,
                "target_id": target_id,
                "link_type": link_type,
            })
            return {"link_id": 99, "source_id": source_id, "target_id": target_id,
                    "link_type": link_type, "created": True}

        envelope = {
            "op": "capture",
            "text": "A thought worth linking to another atom.",
            "links": [
                {"target_id": "brain-target-A", "link_type": "verifies"},
                {"target_id": "brain-target-B", "link_type": "resolves"},
            ],
        }
        result = _run_pi(envelope, {"capture": spy_capture, "add_link": spy_add_link})
        assert "error" not in result
        assert result["thought_id"] == "brain-new-atom"
        assert len(capture_calls) == 1
        assert len(link_calls) == 2
        assert link_calls[0]["source_id"] == "brain-new-atom"
        assert link_calls[0]["target_id"] == "brain-target-A"
        assert link_calls[0]["link_type"] == "verifies"
        assert link_calls[1]["target_id"] == "brain-target-B"
        assert link_calls[1]["link_type"] == "resolves"
        # links written back into result
        assert "links" in result
        assert len(result["links"]) == 2

    def test_capture_with_links_string_format(self):
        """capture op with links=["id:type"] string format calls add_link."""
        link_calls = []

        def spy_capture(conn, text, user_id, **kwargs):
            return {"thought_id": "brain-atom-str", "summary": "s", "type": "insight",
                    "topics": [], "people": [], "action_items": []}

        def spy_add_link(conn, source_id, target_id, link_type, user_id, **kwargs):
            link_calls.append({"target_id": target_id, "link_type": link_type})
            return {"link_id": 1, "source_id": source_id, "target_id": target_id,
                    "link_type": link_type, "created": True}

        result = _run_pi(
            {"op": "capture", "text": "test", "links": ["brain-xyz:derives_from"]},
            {"capture": spy_capture, "add_link": spy_add_link},
        )
        assert "error" not in result
        assert len(link_calls) == 1
        assert link_calls[0]["target_id"] == "brain-xyz"
        assert link_calls[0]["link_type"] == "derives_from"

    def test_capture_with_invalid_link_type_returns_error(self):
        """capture op with unknown link_type in links list returns error without capturing."""
        capture_calls = []

        def spy_capture(conn, text, user_id, **kwargs):
            capture_calls.append(1)
            return {"thought_id": "brain-should-not-be-called", "summary": "s",
                    "type": "insight", "topics": [], "people": [], "action_items": []}

        captured = io.StringIO()
        with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
             mock.patch.object(open_brain, "capture", side_effect=spy_capture), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "op": "capture",
                 "text": "test",
                 "links": [{"target_id": "brain-t", "link_type": "NOT_A_REAL_TYPE"}],
             }))), \
             mock.patch("sys.stdout", captured):
            open_brain._run_from_pi()
        data = json.loads(captured.getvalue().strip())
        assert "error" in data
        assert "link spec errors" in data["error"]

    def test_capture_without_links_unchanged(self):
        """capture op with no links list behaves identically to before T3.1."""
        def spy_capture(conn, text, user_id, **kwargs):
            return {"thought_id": "brain-plain", "summary": "s", "type": "insight",
                    "topics": [], "people": [], "action_items": []}

        result = _run_pi(
            {"op": "capture", "text": "plain capture no links"},
            {"capture": spy_capture},
        )
        assert "error" not in result
        assert result["thought_id"] == "brain-plain"
        assert "links" not in result


# ─── unknown op ───────────────────────────────────────────────────────────────


def test_unknown_op_returns_error():
    """An unrecognised op returns {"error": "Unknown op: ..."} JSON."""
    captured = io.StringIO()
    with mock.patch("open_brain._connect", return_value=mock.MagicMock()), \
         mock.patch("sys.stdin", io.StringIO(json.dumps({"op": "definitely_not_an_op"}))), \
         mock.patch("sys.stdout", captured):
        open_brain._run_from_pi()
    data = json.loads(captured.getvalue().strip())
    assert "error" in data
    assert "definitely_not_an_op" in data["error"]
