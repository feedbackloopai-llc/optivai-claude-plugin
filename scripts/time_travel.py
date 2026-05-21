"""brain-W2-S3: Inspect-memory (time-travel queries).

Queries ``brain.thought_versions`` for the historical state of a thought:

  - by ISO timestamp: returns the version with the largest ``created_at``
    that is ``<= target``. Returns ``None`` when no version exists at or
    before that time.
  - by revision number: returns the exact revision. Returns ``None`` when
    that revision does not exist.
  - latest: returns the version with the highest revision. Returns ``None``
    when the thought has never been snapshotted.

PS scoping (Principal Scoping per Lin/Li/Chen 2026 §12.1) is enforced at
every entry point: queries against a thought not in the caller's user
scope raise :class:`RuntimeError` (NOT ``None`` — absence of scope is
distinct from absence of data).

RB invariant exposed via inspection: ``rollback_thought`` appends a new
revision with ``prov_activity='rollback'`` and the rolled-back content;
it never rewrites earlier revisions. Querying the original revision
number after a rollback still returns the ORIGINAL data.

Reference: ``optivai-builder/src/agents/time-travel.ts`` (TypeScript
``InspectMemory`` class). Counterfactual replay deliberately deferred —
see the S3 spec.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class InspectResult:
    """Historical state of a thought at a point in time / revision.

    Mirrors the row columns of ``brain.thought_versions`` plus the
    metadata describing how the row was found (``query_kind`` /
    ``query_value``). ``raw_text`` is the FULL captured text at that
    revision — it is NOT truncated here (callers may truncate for
    display).
    """

    thought_id: str
    revision: int                       # the revision returned
    raw_text: str
    summary: Optional[str]
    thought_type: Optional[str]
    topics: Optional[List[Any]]
    people: Optional[List[Any]]
    action_items: Optional[List[Any]]
    prov_agent: str                     # version-level PROV agent
    prov_activity: str                  # 'snapshot' | 'rollback' | 'update' | ...
    parent_version: Optional[int]
    created_at: Optional[str]           # ISO timestamp
    query_kind: str                     # 'at-timestamp' | 'at-revision' | 'latest'
    query_value: Optional[str] = None   # the queried timestamp or revision number


def _parse_iso_timestamp(s: str) -> datetime:
    """Parse an ISO 8601 timestamp string.

    Tolerant of trailing ``Z`` (which ``datetime.fromisoformat`` did not
    accept before Python 3.11, and which still appears in many serialized
    timestamps). Raises :class:`RuntimeError` if the string cannot be
    parsed.
    """
    if not isinstance(s, str):
        raise RuntimeError(
            f"_parse_iso_timestamp: expected str, got {type(s).__name__}"
        )
    s = s.strip()
    if not s:
        raise RuntimeError("_parse_iso_timestamp: empty timestamp string")
    # Normalize ``Z`` suffix to explicit UTC offset for fromisoformat.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise RuntimeError(
            f"_parse_iso_timestamp: cannot parse '{s}' as ISO 8601 (e.g., "
            f"'2026-05-21T10:30:00+00:00' or '2026-05-21T10:30:00Z')"
        ) from e


def _assert_in_scope(conn, thought_id: str, user_id: str, fn: str) -> None:
    """Raise :class:`RuntimeError` if ``thought_id`` is not in ``user_id``'s scope."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
            (thought_id, user_id),
        )
        if cur.fetchone() is None:
            raise RuntimeError(
                f"{fn}: thought {thought_id} not in user scope (user={user_id})"
            )
    finally:
        cur.close()


def inspect_at_timestamp(
    conn,
    thought_id: str,
    user_id: str,
    at_iso: str,
) -> Optional[InspectResult]:
    """Return the latest version of ``thought_id`` with ``created_at <= at_iso``.

    Returns ``None`` if no version exists at or before the given timestamp
    (e.g., the timestamp predates the first snapshot, or the thought has
    never been snapshotted).

    Raises :class:`RuntimeError` if:
      - ``at_iso`` is not parseable as ISO 8601, OR
      - ``thought_id`` is not in ``user_id``'s scope (PS scoping).
    """
    at_dt = _parse_iso_timestamp(at_iso)
    _assert_in_scope(conn, thought_id, user_id, "inspect_at_timestamp")

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT version_id, revision, raw_text, summary, thought_type,
                   topics, people, action_items,
                   prov_agent, prov_activity, parent_version, created_at
            FROM brain.thought_versions
            WHERE thought_id=%s AND created_at <= %s
            ORDER BY revision DESC
            LIMIT 1
            """,
            (thought_id, at_dt),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        return None
    return _row_to_result(
        thought_id, row, query_kind="at-timestamp", query_value=at_iso
    )


def inspect_at_revision(
    conn,
    thought_id: str,
    user_id: str,
    revision: int,
) -> Optional[InspectResult]:
    """Return the specific revision of ``thought_id``.

    Returns ``None`` if the revision does not exist (e.g., the thought
    has fewer revisions than requested).

    Raises :class:`RuntimeError` if ``thought_id`` is not in
    ``user_id``'s scope (PS scoping).
    """
    if not isinstance(revision, int):
        raise RuntimeError(
            f"inspect_at_revision: revision must be int, got "
            f"{type(revision).__name__}"
        )
    _assert_in_scope(conn, thought_id, user_id, "inspect_at_revision")

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT version_id, revision, raw_text, summary, thought_type,
                   topics, people, action_items,
                   prov_agent, prov_activity, parent_version, created_at
            FROM brain.thought_versions
            WHERE thought_id=%s AND revision=%s
            """,
            (thought_id, revision),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        return None
    return _row_to_result(
        thought_id, row, query_kind="at-revision", query_value=str(revision)
    )


def inspect_latest(
    conn,
    thought_id: str,
    user_id: str,
) -> Optional[InspectResult]:
    """Return the version with the highest revision number.

    Returns ``None`` if no versions exist (thought captured but never
    snapshotted).

    Raises :class:`RuntimeError` if ``thought_id`` is not in
    ``user_id``'s scope (PS scoping).
    """
    _assert_in_scope(conn, thought_id, user_id, "inspect_latest")

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT version_id, revision, raw_text, summary, thought_type,
                   topics, people, action_items,
                   prov_agent, prov_activity, parent_version, created_at
            FROM brain.thought_versions
            WHERE thought_id=%s
            ORDER BY revision DESC
            LIMIT 1
            """,
            (thought_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        return None
    return _row_to_result(
        thought_id, row, query_kind="latest", query_value=None
    )


def _row_to_result(
    thought_id: str,
    row: tuple,
    query_kind: str,
    query_value: Optional[str] = None,
) -> InspectResult:
    """Convert a thought_versions row tuple into an :class:`InspectResult`."""
    (
        _version_id,
        revision,
        raw_text,
        summary,
        thought_type,
        topics,
        people,
        action_items,
        prov_agent,
        prov_activity,
        parent_version,
        created_at,
    ) = row
    return InspectResult(
        thought_id=thought_id,
        revision=revision,
        raw_text=raw_text,
        summary=summary,
        thought_type=thought_type,
        topics=topics,
        people=people,
        action_items=action_items,
        prov_agent=prov_agent,
        prov_activity=prov_activity,
        parent_version=parent_version,
        created_at=created_at.isoformat() if created_at else None,
        query_kind=query_kind,
        query_value=query_value,
    )


def inspect_result_to_dict(result: InspectResult) -> Dict[str, Any]:
    """Convert an :class:`InspectResult` to a plain dict (JSON-serializable)."""
    return asdict(result)
