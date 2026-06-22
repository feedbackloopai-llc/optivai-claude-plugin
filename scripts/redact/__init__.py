"""Defense-in-depth redaction pipeline for Open Brain v0.2.1.

Vendored from gz-redact (https://github.com/membersuite-private/gz-redact)
on 2026-05-22 — bead redact-S1+S2. Tokenizing redactor, Presidio bridge,
tenant registry, and GrowthZone-specific recognizers are NOT included;
this is the dev-tool-memory-substrate subset.

Public API:
    from scripts.redact import (
        redact, compose,
        secrets_redactors, pii_redactors,
        EntropyRedactor, ContextRedactor,
    )
"""
from .types import (
    Span,
    Detection,
    Confidence,
    RedactionHit,
    Redactor,
)
from .regex_redactor import RegexRedactor
from .compose import compose, redact, gather_hits
from .recognizers import (
    secrets_redactors,
    pii_redactors,
    EntropyRedactor,
    ContextRedactor,
)

__all__ = [
    "Span", "Detection", "Confidence",
    "RedactionHit", "Redactor",
    "RegexRedactor", "compose", "redact", "gather_hits",
    "secrets_redactors", "pii_redactors",
    "EntropyRedactor", "ContextRedactor",
]
