#!/usr/bin/env python3
"""fblai-hlvnk — add_link target ownership enforcement tests.

atom_links has NO FK on target_id by design (targets may be bead IDs or
external refs outside brain.thoughts).  The fix enforces ownership only when
the target IS a brain atom that exists in brain.thoughts.

Cases:
  (a) User's atom A links to user's own atom B → allowed.
  (b) Linking to a bead id ("gz-xxxxx") → allowed (external target by design).
  (c) Linking to a brain atom owned by a DIFFERENT user → rejected.
  (d) Linking to a non-existent brain-id → allowed (orphan target, no-FK design).
  (e) fblai-/optivai- prefixed bead IDs → allowed.
  (f) verify_source_exists=False (capture-links fast-path) with own target → allowed.

All tests use mocked connections — no live DB required.

Run: python3 -m pytest scripts/tests/test_add_link_ownership.py -v
"""
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_cursor_factory(fetchone_results):
    """Return a mock cursor whose fetchone() calls return items from the list in order."""
    cur = mock.MagicMock()
    cur.__enter__ = mock.MagicMock(return_value=cur)
    cur.__exit__ = mock.MagicMock(return_value=False)
    cur.fetchone.side_effect = list(fetchone_results)
    return cur


def _make_conn(cursor_mock):
    """Return a mock connection that returns cursor_mock from cursor()."""
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor_mock
    return conn


# ─── Test (a): user's own atom → allowed ─────────────────────────────────────


def test_own_atom_to_own_atom_allowed():
    """User links their own atom A to their own atom B: allowed."""
    user = "alice"
    source_id = "brain-111-aaaaaa"
    target_id = "brain-222-bbbbbb"

    # fetchone sequence:
    #   1. verify_source_exists check → (1,) = found
    #   2. target ownership check (SELECT user_id) → (user,) = same user
    cur = _make_cursor_factory([(1,), (user,)])
    conn = _make_conn(cur)

    # Mock the INSERT RETURNING + ON CONFLICT path: first call returns None
    # (simulate ON CONFLICT DO NOTHING), then re-fetch returns a row.
    # Easiest: override fetchone to return (1,) for source check, then ('alice',)
    # for target check, then (42,) for INSERT RETURNING.
    cur.fetchone.side_effect = [
        (1,),      # source exists
        (user,),   # target user_id
        (42,),     # INSERT RETURNING link_id
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="cites",
        user_id=user,
    )
    assert result["source_id"] == source_id
    assert result["target_id"] == target_id
    assert result["created"] is True


# ─── Test (b): bead id gz- prefix → allowed ──────────────────────────────────


def test_bead_target_gz_prefix_allowed():
    """Linking to a gz- bead ID must be allowed (external target, no ownership check)."""
    user = "alice"
    source_id = "brain-333-cccccc"
    target_id = "gz-abc123"  # bead ID — no brain.thoughts row expected

    # fetchone sequence: source check → found; target check must NOT be issued
    # (target starts with gz-, not brain-); INSERT → link created.
    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),    # source exists
        (42,),   # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="references_bead",
        user_id=user,
    )
    assert result["target_id"] == target_id
    assert result["created"] is True

    # Verify no SELECT user_id query was issued for the bead target
    calls = [str(c) for c in cur.execute.call_args_list]
    ownership_queries = [c for c in calls if "SELECT user_id" in c]
    assert len(ownership_queries) == 0, (
        f"No ownership query should be issued for gz- bead targets; "
        f"calls: {calls}"
    )


def test_bead_target_fblai_prefix_allowed():
    """Linking to an fblai- bead ID must be allowed."""
    user = "alice"
    source_id = "brain-444-dddddd"
    target_id = "fblai-xyz789"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),   # source exists
        (42,),  # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="references_bead",
        user_id=user,
    )
    assert result["target_id"] == target_id
    assert result["created"] is True


def test_bead_target_optivai_prefix_allowed():
    """Linking to an optivai- bead ID must be allowed."""
    user = "alice"
    source_id = "brain-555-eeeeee"
    target_id = "optivai-qrs456"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),   # source exists
        (42,),  # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="references_bead",
        user_id=user,
    )
    assert result["target_id"] == target_id


# ─── Test (c): brain atom owned by different user → rejected ─────────────────


def test_cross_user_brain_atom_target_rejected():
    """Linking to a brain atom owned by a DIFFERENT user must be rejected."""
    caller = "alice"
    attacker_target = "brain-666-ffffff"  # owned by "bob"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),     # source exists (caller owns source)
        ("bob",), # target user_id = different user → should trigger rejection
    ]

    with pytest.raises(RuntimeError) as exc_info:
        open_brain.add_link(
            conn,
            source_id="brain-777-gggggg",
            target_id=attacker_target,
            link_type="cites",
            user_id=caller,
        )

    error_msg = str(exc_info.value).lower()
    assert any(word in error_msg for word in (
        "different user", "cross-user", "not permitted", "ownership",
        "belongs to", "permission"
    )), (
        f"Error must describe cross-user link rejection; got: {exc_info.value!r}"
    )


def test_cross_user_rejection_does_not_insert():
    """After cross-user target rejection, no INSERT must have been issued."""
    caller = "alice"
    source_id = "brain-888-hhhhhh"
    target_id = "brain-999-iiiiii"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),     # source exists
        ("bob",), # target owned by bob
    ]

    with pytest.raises(RuntimeError):
        open_brain.add_link(
            conn,
            source_id=source_id,
            target_id=target_id,
            link_type="cites",
            user_id=caller,
        )

    # Verify INSERT was never called
    insert_calls = [
        c for c in cur.execute.call_args_list
        if "INSERT" in str(c)
    ]
    assert len(insert_calls) == 0, (
        f"INSERT must not be called after cross-user rejection; calls: {cur.execute.call_args_list}"
    )


# ─── Test (d): non-existent brain-id target → allowed (orphan) ───────────────


def test_nonexistent_brain_id_target_allowed():
    """Linking to a brain-id that does NOT exist in brain.thoughts is allowed.

    The no-FK design permits orphan targets (forward-references, deleted atoms).
    The ownership check must only fire when the atom EXISTS.
    """
    user = "alice"
    source_id = "brain-aaa-jjjjjj"
    nonexistent_target = "brain-bbb-kkkkkk"  # does not exist

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),   # source exists
        None,   # SELECT user_id FROM brain.thoughts WHERE thought_id = nonexistent → None
        (42,),  # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=nonexistent_target,
        link_type="cites",
        user_id=user,
    )
    assert result["target_id"] == nonexistent_target
    assert result["created"] is True


# ─── Test (e): verify_source_exists=False fast-path + own target ─────────────


def test_verify_source_false_fastpath_own_target_allowed():
    """capture-links fast-path (verify_source_exists=False) with own brain target → allowed."""
    user = "alice"
    source_id = "brain-ccc-llllll"
    target_id = "brain-ddd-mmmmmm"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    # No source-exists check (verify_source_exists=False).
    # Ownership check: target exists and belongs to same user.
    cur.fetchone.side_effect = [
        (user,),  # SELECT user_id for target → same user
        (42,),    # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="cites",
        user_id=user,
        verify_source_exists=False,
    )
    assert result["created"] is True


def test_verify_source_false_fastpath_bead_target_allowed():
    """capture-links fast-path with bead target → allowed (no ownership check)."""
    user = "alice"
    source_id = "brain-eee-nnnnnn"
    target_id = "gz-bead99"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (42,),  # INSERT RETURNING (no source check, no ownership check for gz-)
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="references_bead",
        user_id=user,
        verify_source_exists=False,
    )
    assert result["target_id"] == target_id


# ─── Test (f): unknown external ref (no brain- prefix) → allowed ─────────────


def test_unknown_external_target_allowed():
    """An external ref that is not a bead ID and not a brain ID is allowed."""
    user = "alice"
    source_id = "brain-fff-oooooo"
    # Hypothetical external ID with no recognized prefix
    target_id = "ext-some-external-ref-123"

    cur = mock.MagicMock()
    conn = _make_conn(cur)
    cur.fetchone.side_effect = [
        (1,),   # source exists
        (42,),  # INSERT RETURNING
    ]

    result = open_brain.add_link(
        conn,
        source_id=source_id,
        target_id=target_id,
        link_type="cites",
        user_id=user,
    )
    assert result["target_id"] == target_id
