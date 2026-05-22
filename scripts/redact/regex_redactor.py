# Vendored from gz-redact on 2026-05-22 — bead redact-S1/S2.
"""Pattern-based redactor. Wraps a regex and an optional post-match validator.

Mirrors Pi-Plugin/src/redact/regex-redactor.ts.

The regex MUST have the global flag (in Python: use re.finditer() or
re.findall()). The optional validator receives the full re.Match object so it
can inspect named groups via match.groupdict() if the regex uses them. Most
validators only need match.group(0) (the full match string). The validator must
return True to keep the match; otherwise the match is dropped. Use this for
checksum-based filtering (Luhn for PANs, AWS key prefix verification, etc.).

Confidence must be in [0.0, 1.0] and defaults to 0.95.

This file exposes TWO surfaces:

1. _SingleRegexRedactor (vendored verbatim from gz-redact): one pattern,
   one category, optional validator. Used as the internal building block.

2. RegexRedactor (slim Open Brain v0.2.1 surface): accepts a list of
   (pattern, category, replacement, confidence) tuples and composes them
   into a single Redactor protocol implementation.
"""
from __future__ import annotations

import math
import re
from typing import Callable, Optional, Sequence, Tuple, Union

from .types import RedactionHit, Redactor

DEFAULT_CONFIDENCE = 0.95


class _SingleRegexRedactor:
    """Single-pattern redactor implementing the Redactor protocol.

    This is the upstream gz-redact RegexRedactor implementation, vendored
    verbatim. The new public RegexRedactor wraps a list of these.

    Attributes:
        category: Dotted-notation category ID (e.g., "secret.aws.access_key").
        pattern: Compiled regex pattern. Must be a Pattern object.
        validator: Optional function that receives re.Match and returns bool.
        confidence: Probability estimate in [0.0, 1.0].
        replacement: Replacement string substituted for matched spans.
    """

    def __init__(
        self,
        category: str,
        pattern: re.Pattern,
        validator: Optional[Callable[[re.Match], bool]] = None,
        confidence: float = DEFAULT_CONFIDENCE,
        replacement: Optional[str] = None,
    ) -> None:
        """Initialize a _SingleRegexRedactor.

        Args:
            category: Non-empty dotted-notation category string.
            pattern: Compiled re.Pattern (typically re.compile("...", re.MULTILINE)).
            validator: Optional callable receiving re.Match, returning bool.
            confidence: Float in [0.0, 1.0]. Raises if outside range or NaN.
            replacement: Override the default "[REDACTED:<category>]" replacement.

        Raises:
            ValueError: If category is empty, confidence is outside [0, 1], or
                confidence is NaN.
        """
        if not isinstance(category, str) or len(category) == 0:
            raise ValueError("RegexRedactor requires a non-empty category")

        if not isinstance(confidence, (int, float)):
            raise ValueError("RegexRedactor confidence must be a number")

        # Check for NaN using math.isnan
        if math.isnan(float(confidence)):
            raise ValueError("RegexRedactor confidence must not be NaN")

        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("RegexRedactor confidence must be in [0.0, 1.0]")

        if pattern is None or not hasattr(pattern, "finditer"):
            raise ValueError("RegexRedactor requires a compiled re.Pattern")

        self.category = category
        self.pattern = pattern
        # Store a default accept-all validator when none is provided. This avoids
        # a branch on every iteration in scan() and keeps the hot path branchless.
        self.validator = validator if validator is not None else lambda m: True
        self.confidence = float(confidence)
        self.replacement = (
            replacement if replacement is not None else f"[REDACTED:{category}]"
        )

    def scan(self, text: str) -> list[RedactionHit]:
        """Scan text and return all matching RedactionHits.

        Args:
            text: The string to scan.

        Returns:
            List of RedactionHit objects (empty list if no matches).
            Never raises on valid string input.
        """
        if not isinstance(text, str) or len(text) == 0:
            return []

        hits: list[RedactionHit] = []

        for m in self.pattern.finditer(text):
            valid: bool = True
            try:
                valid = self.validator(m)
            except Exception:
                # Validator threw. Treat as a rejected match and continue.
                # This preserves the Redactor contract that scan() never throws
                # on valid string input.
                continue

            if not valid:
                continue

            hits.append(
                RedactionHit(
                    start=m.start(),
                    end=m.end(),
                    category=self.category,
                    confidence=self.confidence,
                    replacement=self.replacement,
                )
            )

        return hits


# Type alias for the slim public constructor signature.
RegexRule = Tuple[re.Pattern, str, str, float]


class RegexRedactor:
    """Multi-pattern regex redactor — slim Open Brain v0.2.1 surface.

    Accepts a list of (pattern, category, replacement, confidence) tuples.
    Each tuple becomes one internal _SingleRegexRedactor; scan() concatenates
    their outputs.

    Example:
        r = RegexRedactor([
            (re.compile(r"foo\\d+"), "test.foo", "[FOO]", 0.9),
            (re.compile(r"bar\\d+"), "test.bar", "[BAR]", 0.9),
        ])
    """

    def __init__(self, rules: Sequence[RegexRule]) -> None:
        """Initialize a RegexRedactor from a list of rule tuples.

        Args:
            rules: A sequence of (pattern, category, replacement, confidence)
                tuples. Each tuple must contain exactly 4 elements.

        Raises:
            ValueError: If rules is not a sequence, any rule is malformed,
                or any contained rule fails _SingleRegexRedactor validation.
        """
        if rules is None:
            raise ValueError("RegexRedactor requires a list of rules (got None)")

        # Allow Sequence (list/tuple) but reject raw strings (a string is also
        # a sequence in Python and would silently iterate per-character).
        if isinstance(rules, (str, bytes)):
            raise ValueError(
                "RegexRedactor requires a sequence of rule tuples, "
                "not a string"
            )

        self._inner: list[_SingleRegexRedactor] = []
        for idx, rule in enumerate(rules):
            if not isinstance(rule, tuple) or len(rule) != 4:
                raise ValueError(
                    f"RegexRedactor rule at index {idx} must be a "
                    f"(pattern, category, replacement, confidence) 4-tuple"
                )
            pattern, category, replacement, confidence = rule
            self._inner.append(
                _SingleRegexRedactor(
                    category=category,
                    pattern=pattern,
                    validator=None,
                    confidence=confidence,
                    replacement=replacement,
                )
            )

    def scan(self, text: str) -> list[RedactionHit]:
        """Scan text against every internal rule and return all hits.

        Args:
            text: The string to scan.

        Returns:
            Concatenated list of RedactionHit objects from all internal
            single-pattern redactors. Empty list if no matches or empty input.
            Never raises on valid string input.
        """
        if not isinstance(text, str) or len(text) == 0:
            return []

        hits: list[RedactionHit] = []
        for r in self._inner:
            hits.extend(r.scan(text))
        return hits
