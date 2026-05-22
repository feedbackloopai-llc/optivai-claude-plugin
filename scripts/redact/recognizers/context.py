# Vendored from gz-redact on 2026-05-22 — bead redact-S6.
"""Context-aware redactor wrapper.

Mirrors Pi-Plugin/src/redact/recognizers/context.ts.

Wraps an inner Redactor. For each hit, checks whether any configured keyword
appears within `radius` characters of the hit's start. If yes, emits the hit
with confidence bumped to upgradedConfidence. If no, emits the hit unchanged.
Hit category and replacement are preserved.

Designed for entropy redactor confidence upgrade: a high-entropy token is
low-confidence in isolation (0.55), but if "password" or "api_key" is within
10 chars, confidence upgrades to 0.85.

Keywords are matched case-insensitively as substrings.
"""
from __future__ import annotations

from typing import Optional

from ..types import RedactionHit, Redactor


class ContextRedactor:
    """Context-aware wrapper for confidence upgrading.

    Checks whether any configured keyword appears within a radius of each hit.
    If yes, upgrades confidence to upgradedConfidence.
    """

    def __init__(
        self,
        *,
        inner: Redactor,
        keywords: list[str],
        radius: int = 10,
        upgraded_confidence: float = 0.85,
        suppress_low_confidence: float | None = None,
    ) -> None:
        """Initialize a ContextRedactor.

        Args:
            inner: The wrapped redactor to scan with.
            keywords: Non-empty list of keywords to search for (case-insensitive).
            radius: Search radius (characters) around hit start. Default 10.
            upgraded_confidence: Confidence to use if keyword found. Default 0.85.
            suppress_low_confidence: Optional threshold. Hits with confidence below
                this threshold and no keyword context found are suppressed (dropped).
                Default None (no suppression).

        Raises:
            ValueError: If keywords is empty, radius < 0, upgraded_confidence
                is out of [0.0, 1.0], or suppress_low_confidence is out of [0.0, 1.0].
        """
        if not keywords or len(keywords) == 0:
            raise ValueError("keywords must be a non-empty list")
        if radius < 0:
            raise ValueError("radius must be >= 0")
        if upgraded_confidence < 0 or upgraded_confidence > 1:
            raise ValueError("upgraded_confidence must be in [0.0, 1.0]")
        if suppress_low_confidence is not None:
            if suppress_low_confidence < 0 or suppress_low_confidence > 1:
                raise ValueError("suppress_low_confidence must be in [0.0, 1.0] or None")

        self.inner = inner
        self.keywords = keywords
        self.keywords_lower = [kw.lower() for kw in keywords]
        self.radius = radius
        self.upgraded_confidence = upgraded_confidence
        self.suppress_low_confidence = suppress_low_confidence

        # Cache the max keyword length plus radius once. Used to size the
        # search window for every hit. Avoids per-hit max computation.
        self.expand_radius = radius + max(len(kw) for kw in self.keywords_lower)

    def scan(self, text: str) -> list[RedactionHit]:
        """Scan text and upgrade confidence for hits near keywords.

        Args:
            text: The string to scan.

        Returns:
            List of RedactionHit objects with upgraded confidence where keywords found,
            or suppressed if suppress_low_confidence is set and conditions match.
        """
        inner_hits = self.inner.scan(text)
        out: list[RedactionHit] = []

        for hit in inner_hits:
            # Size the search window around the hit.
            window_start = max(0, hit.start - self.expand_radius)
            window_end = min(len(text), hit.end + self.expand_radius)
            window = text[window_start:window_end].lower()

            # Check if any keyword appears in the window.
            keyword_matched = any(kw in window for kw in self.keywords_lower)

            if keyword_matched:
                # Upgrade confidence.
                out.append(
                    RedactionHit(
                        start=hit.start,
                        end=hit.end,
                        category=hit.category,
                        confidence=self.upgraded_confidence,
                        replacement=hit.replacement,
                    )
                )
            else:
                # Check if we should suppress low-confidence hits without context.
                if self.suppress_low_confidence is not None and hit.confidence < self.suppress_low_confidence:
                    # Suppress this hit; don't add it to output.
                    pass
                else:
                    # Keep the hit unchanged.
                    out.append(hit)

        return out
