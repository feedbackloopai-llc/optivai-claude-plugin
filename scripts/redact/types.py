# Vendored from gz-redact on 2026-05-22 — bead redact-S1/S2.
"""Shared types for the layered redactor.

Mirrors the TypeScript types in Pi-Plugin/src/redact/types.ts. Same contract,
same field semantics, same invariants.

This module exposes TWO surfaces:

1. The vendored protocol surface (RedactionHit + Redactor) — used internally
   by the regex_redactor + compose modules. These mirror the upstream
   gz-redact public API.

2. The slim consumer surface (Span + Detection + Confidence) — used by Open
   Brain v0.2.1 callers that want a cleaner dataclass model without exposing
   raw integer offsets. Detection wraps a Span + category + confidence enum
   + replacement string. Confidence is an enum (LOW/MEDIUM/HIGH) that maps
   to the underlying float buckets.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RedactionHit:
    """A single redaction detection result emitted by Redactor.scan().

    Mirrors the TypeScript RedactionHit type. Fields are immutable
    (frozen=True) at runtime, preventing accidental mutation by downstream
    consumers that share the same hits list.

    Attributes:
        start: Character offset where the redacted span begins (inclusive).
        end: Character offset where the redacted span ends (exclusive).
        category: Dotted-notation category ID from the taxonomy
            (e.g., "secret.aws.access_key", "pii.contact.email").
        confidence: Recognizer's estimated probability that the span is a
            true positive, in [0.0, 1.0] linear (not logit-space).
        replacement: The string that will substitute the matched span in
            the redacted output (e.g., "[REDACTED:secret.aws.access_key]"
            or "[MEMBER_42]").
    """

    start: int
    end: int
    category: str
    confidence: float
    replacement: str


@runtime_checkable
class Redactor(Protocol):
    """Pluggable redaction component.

    Implementations:
        1. Return an empty list (never None) when nothing matches.
        2. Return hits with start < end and 0 <= start <= len(text).
        3. Use a category from the taxonomy (no ad-hoc category names).
        4. Never raise on valid string input (return [] on no match).
    """

    def scan(self, text: str) -> list[RedactionHit]:
        ...


# ---------------------------------------------------------------------------
# Slim consumer surface: Span / Detection / Confidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A character-offset span within a string.

    Attributes:
        start: Inclusive start offset.
        end: Exclusive end offset. May equal start (zero-length insertion-point).
    """

    start: int
    end: int


class Confidence(Enum):
    """Confidence levels for Detection objects.

    Maps to underlying float buckets used by the RegexRedactor:
        LOW:    0.50 (default lower-bound)
        MEDIUM: 0.75
        HIGH:   0.95 (default upper-bound)
    """

    LOW = 0.50
    MEDIUM = 0.75
    HIGH = 0.95


@dataclass(frozen=True)
class Detection:
    """A higher-level detection result built on Span + Confidence enum.

    Used by the slim public surface (Open Brain v0.2.1) for callers that
    prefer a typed enum over raw float confidence.

    Attributes:
        span: Character offset range of the detection.
        category: Dotted-notation taxonomy category.
        confidence: Confidence enum level.
        replacement: String to substitute in for the detected span.
    """

    span: Span
    category: str
    confidence: Confidence
    replacement: str
