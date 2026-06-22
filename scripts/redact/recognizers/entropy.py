# Vendored from gz-redact on 2026-05-22 — bead redact-S5.
"""Entropy-based redactor for high-entropy strings.

Mirrors Pi-Plugin/src/redact/recognizers/entropy.ts.

EntropyRedactor catches high-entropy strings that don't match specific patterns.
Used as a fallback when specific recognizers miss. Default confidence 0.55 (low,
heuristic-based).

Shannon entropy (in bits per character):
  H = -sum(p_i * log2(p_i)) for each unique character i with frequency p_i.

Returns 0 for empty string or all-same-char strings.
Returns 1.0 for a two-character even split ("ab").
Returns 4.0+ for high-entropy strings like base64 or API keys.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from ..types import RedactionHit, Redactor


def shannon_entropy(s: str) -> float:
    """Compute the Shannon entropy (in bits per character) of a string.

    Args:
        s: The string to analyze.

    Returns:
        Shannon entropy H in bits per character. Returns 0 for empty string.
    """
    if len(s) == 0:
        return 0.0

    # Compute frequency of each character.
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1

    # Compute entropy: H = -sum(p_i * log2(p_i))
    entropy = 0.0
    for count in freq.values():
        p = count / len(s)
        entropy -= p * math.log2(p)

    return entropy


@dataclass
class EntropyRedactorOpts:
    """Options for EntropyRedactor.

    Attributes:
        min_len: Minimum token length to scan. Default 20.
        min_entropy: Minimum Shannon entropy (bits/char) to flag. Default 4.5.
        confidence: Confidence score for hits. Default 0.55 (low confidence).
        category: Category label. Default "secret.unknown.high_entropy".
    """

    min_len: int = 20
    min_entropy: float = 4.5
    confidence: float = 0.55
    category: str = "secret.unknown.high_entropy"


class EntropyRedactor:
    """High-entropy string redactor.

    Scans for candidates matching /[A-Za-z0-9+/=_-]{minLen,}/,
    computes Shannon entropy for each, and flags those with H >= minEntropy.
    """

    def __init__(self, opts: Optional[EntropyRedactorOpts] = None) -> None:
        """Initialize an EntropyRedactor.

        Args:
            opts: Configuration options. Defaults provided if None.

        Raises:
            ValueError: If any option is out of valid range.
        """
        opts = opts or EntropyRedactorOpts()
        self.min_len = opts.min_len
        self.min_entropy = opts.min_entropy
        self.confidence = opts.confidence
        self.category = opts.category

        # Validate parameters
        if self.min_len < 1:
            raise ValueError("min_len must be >= 1")
        if self.min_entropy < 0:
            raise ValueError("min_entropy must be >= 0")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("confidence must be in [0.0, 1.0]")

    def scan(self, text: str) -> list[RedactionHit]:
        """Scan text for high-entropy strings.

        Args:
            text: The string to scan.

        Returns:
            List of RedactionHit objects for high-entropy spans.
        """
        hits: list[RedactionHit] = []
        pattern = re.compile(f"[A-Za-z0-9+/=_-]{{{self.min_len},}}")

        for m in pattern.finditer(text):
            token = m.group(0)
            entropy = shannon_entropy(token)

            if entropy >= self.min_entropy:
                hits.append(
                    RedactionHit(
                        start=m.start(),
                        end=m.end(),
                        category=self.category,
                        confidence=self.confidence,
                        replacement=f"[REDACTED:{self.category}]",
                    )
                )

        return hits
