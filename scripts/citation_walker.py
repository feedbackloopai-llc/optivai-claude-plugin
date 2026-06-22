"""brain-W2-S1: Citation walker.

Walks the ``brain.thoughts.was_derived_from`` FK chain to produce a provenance
tree. Each node is annotated with PROV-DM 1.3 fields (``prov_agent``,
``prov_activity``, ``was_generated_by``). The walker terminates at:

  - ``was_derived_from IS NULL`` (true original — root of derivation chain)
  - A parent that no longer exists in the caller's user scope (sentinel
    ``orphan_marker='orphaned'``) — typically a cross-user reference; a parent
    forgotten via VF_eps has its child's ``was_derived_from`` set to NULL by
    the FK ``ON DELETE SET NULL`` and so does NOT reach the orphan branch.
  - ``depth >= max_depth`` (sentinel ``orphan_marker='max-depth'``)
  - A cycle (defensive — the schema enforces a DAG via FK + monotonic
    ``thought_id``; sentinel ``orphan_marker='cycle'``)

PS scoping (Principal Scoping per Lin/Li/Chen 2026 §12.1):

  - The starting ``thought_id`` MUST belong to ``user_id``; otherwise raise.
  - Every recursive step re-checks scope. A cross-user mid-chain parent is
    NOT recursed into; the walker emits an ``orphan_marker='orphaned'``
    sentinel and stops that branch.

Reference: ``optivai-builder/src/agents/citation-walker.ts`` (TypeScript port
of the same algorithm, ~329 LOC).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

# Default upper bound on traversal depth. The chain is a DAG so depth is
# bounded by the height of the longest derivation path; 50 is well beyond any
# realistic provenance chain (Wave-1 sees max-depth ~3 in production).
DEFAULT_MAX_DEPTH = 50

# Maximum characters of raw_text we surface in each node's preview field.
# Tuned to be informative for humans without blowing up the JSON payload.
PREVIEW_CHARS = 200


@dataclass
class CitationNode:
    """One node in the provenance tree.

    Children are the immediate ancestors in the derivation chain — the FIRST
    child of a node is the node's ``was_derived_from`` parent (or a sentinel
    if the parent is unreachable / capped). The list is kept as a list to
    leave room for future fan-out (e.g., multi-parent derivation), but the
    current schema is strictly single-parent.
    """

    thought_id: str
    depth: int
    raw_text_preview: str  # truncated to PREVIEW_CHARS
    prov_agent: str
    prov_activity: str
    was_generated_by: str
    was_derived_from: Optional[str]
    source_uri: Optional[str]
    # NAL-lite stv (T2.6 / fblai-eovhe): belief state at this node in the
    # derivation chain.  None for sentinel nodes (orphan/cycle/max-depth).
    stv_frequency: Optional[float] = None
    stv_confidence: Optional[float] = None
    children: List["CitationNode"] = field(default_factory=list)
    # Sentinel cases (None for normal nodes):
    #   "orphaned"  — was_derived_from was set but the parent is not visible
    #                 to the caller (forgotten with the FK NULL-set having not
    #                 fired — shouldn't happen — or cross-user reference)
    #   "cycle"     — same thought_id encountered twice (defensive; FK + DAG
    #                 invariant normally prevents this)
    #   "max-depth" — walker stopped because depth >= max_depth
    orphan_marker: Optional[str] = None


def trace_citation(
    conn,
    thought_id: str,
    user_id: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> CitationNode:
    """Walk the provenance chain starting at ``thought_id`` toward the original.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    thought_id
        Starting thought; the returned tree's root.
    user_id
        Caller scope. PS gate: the starting thought_id MUST belong to this
        user, and every recursive step re-checks scope.
    max_depth
        Stop recursion when depth reaches this value. Default is 50.
        Must be a positive integer.

    Returns
    -------
    CitationNode
        Root of the tree. ``root.children[0]`` is the parent (or a sentinel
        if the parent is unreachable or the depth cap was hit). The chain
        continues recursively through ``children[0].children[0]`` until a
        terminating condition is met.

    Raises
    ------
    RuntimeError
        If the starting thought is not in the caller's scope.
    ValueError
        If ``max_depth`` is non-positive (a 0 or negative cap is nonsense).
    """
    # Validate inputs eagerly. A non-positive max_depth would produce a tree
    # with no useful information and silently mask bugs in the caller.
    if not isinstance(max_depth, int) or max_depth < 1:
        raise ValueError(
            f"trace_citation: max_depth must be a positive int, got {max_depth!r}"
        )
    if not thought_id:
        raise ValueError("trace_citation: thought_id must be a non-empty string")
    if not user_id:
        raise ValueError("trace_citation: user_id must be a non-empty string")

    # PS scope check on the starting thought. We open one cursor for the
    # check and close it before the walk; the walk opens fresh cursors.
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
            (thought_id, user_id),
        )
        if cur.fetchone() is None:
            raise RuntimeError(
                f"trace_citation: thought {thought_id} not in user scope "
                f"(user={user_id})"
            )
    finally:
        cur.close()

    visited: Set[str] = set()
    return _walk(
        conn=conn,
        thought_id=thought_id,
        user_id=user_id,
        depth=0,
        max_depth=max_depth,
        visited=visited,
    )


def _walk(
    conn,
    thought_id: str,
    user_id: str,
    depth: int,
    max_depth: int,
    visited: Set[str],
) -> CitationNode:
    """Build a CitationNode for thought_id; recurse on was_derived_from.

    This is the recursive workhorse. It is intentionally a free function
    rather than a method so the public API (``trace_citation``) has a
    single, clearly-scoped entry point.
    """
    # Cycle detection. Defensive: the schema enforces single-parent + monotonic
    # thought_id so a cycle is structurally impossible, but if a future schema
    # change relaxes that we don't want to recurse infinitely.
    if thought_id in visited:
        return CitationNode(
            thought_id=thought_id,
            depth=depth,
            raw_text_preview="[cycle]",
            prov_agent="",
            prov_activity="",
            was_generated_by="",
            was_derived_from=None,
            source_uri=None,
            orphan_marker="cycle",
        )
    visited.add(thought_id)

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT raw_text, prov_agent, prov_activity, was_generated_by,
                   was_derived_from, source_uri, stv_frequency, stv_confidence
              FROM brain.thoughts
             WHERE thought_id=%s AND user_id=%s
            """,
            (thought_id, user_id),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        # Parent referenced but not visible in caller's scope. Two real-world
        # causes:
        #   1. Cross-user reference (the parent belongs to a different user
        #      and PS scoping hides it).
        #   2. Corrupt data: was_derived_from points to a non-existent row.
        #      The FK ON DELETE SET NULL means a forgotten parent normally
        #      sets the child's was_derived_from to NULL, so this only
        #      happens if the FK was bypassed (direct INSERT/UPDATE) or
        #      disabled.
        return CitationNode(
            thought_id=thought_id,
            depth=depth,
            raw_text_preview="[orphaned]",
            prov_agent="",
            prov_activity="",
            was_generated_by="",
            was_derived_from=None,
            source_uri=None,
            orphan_marker="orphaned",
        )

    raw_text, prov_agent, prov_activity, was_generated_by, was_derived_from, source_uri, stv_f, stv_c = row
    preview = (raw_text or "")[:PREVIEW_CHARS]

    node = CitationNode(
        thought_id=thought_id,
        depth=depth,
        raw_text_preview=preview,
        prov_agent=prov_agent or "",
        prov_activity=prov_activity or "",
        was_generated_by=was_generated_by or "",
        was_derived_from=was_derived_from,
        source_uri=source_uri,
        stv_frequency=float(stv_f) if stv_f is not None else None,
        stv_confidence=float(stv_c) if stv_c is not None else None,
    )

    # Recurse on the parent (was_derived_from) if present. The depth cap is
    # checked BEFORE the recursive call so we never query the DB for a row
    # that would be discarded.
    if was_derived_from is not None:
        next_depth = depth + 1
        if next_depth >= max_depth:
            node.children.append(
                CitationNode(
                    thought_id=was_derived_from,
                    depth=next_depth,
                    raw_text_preview="[max-depth]",
                    prov_agent="",
                    prov_activity="",
                    was_generated_by="",
                    was_derived_from=None,
                    source_uri=None,
                    orphan_marker="max-depth",
                )
            )
        else:
            parent_node = _walk(
                conn=conn,
                thought_id=was_derived_from,
                user_id=user_id,
                depth=next_depth,
                max_depth=max_depth,
                visited=visited,
            )
            node.children.append(parent_node)

    return node


def citation_node_to_dict(node: CitationNode) -> Dict[str, Any]:
    """Convert a CitationNode tree to a plain dict (JSON-serializable).

    ``dataclasses.asdict`` recurses through nested dataclasses (including the
    ``children`` list), so a single call produces the full tree shape. This
    helper exists as a documented seam in case a future revision needs to
    redact preview text, strip orphan branches for external consumers, or
    add computed fields (e.g., chain length).
    """
    return asdict(node)
