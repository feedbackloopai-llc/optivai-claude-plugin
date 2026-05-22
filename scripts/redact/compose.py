# Vendored from gz-redact on 2026-05-22 — bead redact-S1/S2.
"""Composition layer for redactor orchestration and overlap resolution.

Mirrors Pi-Plugin/src/redact/compose.ts.

Provides the core functions for gathering hits from multiple redactors and
applying them to text with proper overlap resolution.

This module exposes:

- gather_hits(text, redactors) — collect + overlap-resolve all hits
- redact(text, redactors)      — apply all redactors to text
- compose(redactors)           — identity composition (returns the list)

The compose() function exists for the slim Open Brain v0.2.1 surface where
callers write `redact(text, compose([r1, r2]))`. It performs argument
validation (rejecting None) and returns the redactors list unchanged.
Conceptually it is the place where future pipeline-time wiring (caching,
metrics, dedup, instrumentation) would attach without changing call sites.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .types import RedactionHit, Redactor


def gather_hits(text: str, redactors: Sequence[Redactor]) -> list[RedactionHit]:
    """Collect all hits from all redactors and resolve overlaps.

    When two hits overlap (their spans intersect), keep the one with:
      1. Higher confidence (primary tiebreaker)
      2. Longer match (secondary tiebreaker for equal confidence)
      3. Earlier start (tertiary tiebreaker)

    Returns hits sorted ASCENDING by start offset (for predictability).

    Args:
        text: The string being scanned.
        redactors: Sequence of Redactor instances to run.

    Returns:
        List of non-overlapping RedactionHit objects sorted by start.
    """
    if not text or redactors is None or len(redactors) == 0:
        return []

    # Collect all hits from all redactors.
    all_hits: list[RedactionHit] = []
    for redactor in redactors:
        all_hits.extend(redactor.scan(text))

    if len(all_hits) == 0:
        return []

    # Resolve overlaps and return sorted by start.
    return _resolve_overlaps(all_hits)


def redact(text: Optional[str], redactors: Sequence[Redactor]) -> Optional[str]:
    """Apply all redactors to text and return the redacted output.

    Implementation:
      1. gather_hits(text, redactors) -> sorted, overlap-resolved hits.
      2. Apply hits in DESCENDING start order (so earlier offsets remain valid
         as we splice).
      3. Return mutated string.

    If text is None, returns None. If no redactors or no hits, returns text
    unchanged.

    Args:
        text: The string to redact (or None).
        redactors: Sequence of Redactor instances to apply.

    Returns:
        Redacted string with all hits replaced by their replacement strings,
        or None if text is None.
    """
    if text is None:
        return None

    if not isinstance(text, str):
        # Defensive: non-string, non-None input returns unchanged.
        return text

    hits = gather_hits(text, redactors if redactors is not None else [])
    if len(hits) == 0:
        return text
    return _apply_redactions(text, hits)


def compose(redactors: Optional[Sequence[Redactor]]) -> list[Redactor]:
    """Compose a list of redactors into a pipeline-ready list.

    The slim Open Brain v0.2.1 surface for pipeline assembly. Today this is
    an identity function over the input (with None coerced to empty list and
    a defensive copy taken). It exists so future pipeline-time concerns
    (caching, metric instrumentation, dedup) can be added at this seam
    without changing call sites that already write
    `redact(text, compose([...]))`.

    Args:
        redactors: A sequence of Redactor instances (or None for empty).

    Returns:
        A list of Redactor instances. Always returns a fresh list, never
        the input reference, so mutation of the returned list cannot affect
        the caller's original sequence.

    Raises:
        ValueError: If redactors is provided but is not a sequence (e.g.,
            a single Redactor instance was passed instead of a list).
    """
    if redactors is None:
        return []

    # Reject strings/bytes — they are sequences but iterating per-character
    # would silently produce garbage.
    if isinstance(redactors, (str, bytes)):
        raise ValueError(
            "compose() requires a sequence of redactors, not a string"
        )

    # Duck-type a sequence (must support len() and iteration).
    try:
        _ = len(redactors)
    except TypeError as exc:
        raise ValueError(
            "compose() requires a sequence with a defined length"
        ) from exc

    return list(redactors)


def _resolve_overlaps(hits: list[RedactionHit]) -> list[RedactionHit]:
    """Resolve overlapping hits by priority: confidence, then length, then position.

    Assumes input is unsorted. Returns sorted ascending by start offset.

    Args:
        hits: List of potentially overlapping RedactionHit objects.

    Returns:
        List of non-overlapping RedactionHit objects sorted by start.
    """
    if len(hits) == 0:
        return []

    # Sort by start asc; tiebreak: longer match first, then higher confidence.
    sorted_hits = sorted(
        hits,
        key=lambda h: (h.start, -(h.end - h.start), -h.confidence),
    )

    kept: list[RedactionHit] = []
    for hit in sorted_hits:
        if not kept or hit.start >= kept[-1].end:
            # No overlap: keep.
            kept.append(hit)
            continue

        # Overlap with last kept. Decide which wins per priority rules:
        #   1. Higher confidence wins.
        #   2. If equal confidence, longer match wins.
        #   3. If equal length, keep the earlier one (kept[-1]).
        last = kept[-1]
        last_len = last.end - last.start
        hit_len = hit.end - hit.start

        if hit.confidence > last.confidence:
            kept[-1] = hit
        elif hit.confidence == last.confidence and hit_len > last_len:
            kept[-1] = hit
        # Otherwise, last stays.

    return kept


def _apply_redactions(text: str, hits: list[RedactionHit]) -> str:
    """Apply a sorted list of non-overlapping hits to text by splicing.

    Sorts hits descending by start so earlier offsets remain valid as we splice.

    Args:
        text: The original string.
        hits: List of non-overlapping RedactionHit objects (any order).

    Returns:
        Text with all hits replaced by their replacement strings.
    """
    # Sort hits descending by start so we can splice without invalidating offsets.
    sorted_hits = sorted(hits, key=lambda h: -h.start)

    out = text
    for h in sorted_hits:
        out = out[: h.start] + h.replacement + out[h.end :]

    return out
