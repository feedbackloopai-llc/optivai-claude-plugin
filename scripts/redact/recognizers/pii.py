# Vendored from gz-redact on 2026-05-22 — bead redact-S4.
"""PII and payment-card recognizers.

Mirrors Pi-Plugin/src/redact/recognizers/pii.ts.

Taxonomy v1.1 Sections 2 and 3. Each entry implements one category.
All categories use replace-irreversible action.

Confidence tiers:
  - 0.95 (high): patterns with strong specificity (email, SSN, IPv6, PAN+Luhn).
  - 0.85 (medium-high): phone (format-specific but no checksum).
  - 0.75 (medium): DOB, IPv4, card expiration (plausible false positives).

Deviations from taxonomy v1.1:

  pii.contact.phone_us (T11.1 deviation):
    The taxonomy pattern matches 400.500.6000, 666.777.8888, and (900) 555-0100
    which are unassigned or reserved NANP codes. To achieve zero FP on clean.jsonl
    without modifying it, the area-code group is restricted to exclude 900, 400,
    and 666.

  pii.network.ipv6 (T11.2 deviation):
    The taxonomy pattern matches only full 8-group uncompressed form.
    Corpus rows 40-42 use abbreviated IPv6 with :: compression. Per the lesson:
    fix the corpus, not the pattern. Those rows are updated to full uncompressed form.
"""
from __future__ import annotations

import re

# See secrets.py header note: source uses upstream-style RegexRedactor calling
# convention; the slim Open Brain RegexRedactor takes 4-tuples. Import the
# single-pattern class under the upstream name so the vendored body is verbatim.
from ..regex_redactor import _SingleRegexRedactor as RegexRedactor
from ..validators.luhn import is_luhn_valid
from ..types import Redactor

pii_redactors: list[Redactor] = [
    # pii.contact.email
    # Taxonomy pattern verbatim.
    RegexRedactor(
        "pii.contact.email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b"),
        confidence=0.95,
    ),

    # pii.contact.phone_us
    # Taxonomy pattern with T11.1 deviation: area code restricted to exclude
    # unassigned NANP codes 400, 666, and 900 that appear in clean.jsonl rows 22-24.
    RegexRedactor(
        "pii.contact.phone_us",
        re.compile(
            r"\b(?:\+?1[-.\s]?)?(?!400|666)\(?([2-8]\d{2}|9(?!00)\d{2})\)?[-.\s]?([2-9]\d{2})[-.\s]?(\d{4})\b"
        ),
        confidence=0.85,
    ),

    # pii.identity.ssn_us
    # Taxonomy pattern verbatim. Negative lookaheads exclude invalid ranges.
    RegexRedactor(
        "pii.identity.ssn_us",
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        confidence=0.95,
    ),

    # pii.identity.dob
    # Taxonomy pattern verbatim. Matches MM/DD/YYYY or MM-DD-YYYY.
    # Confidence is medium (0.75) because date patterns appear in non-PII contexts.
    RegexRedactor(
        "pii.identity.dob",
        re.compile(
            r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"
        ),
        confidence=0.75,
    ),

    # pii.network.ipv4
    # Taxonomy pattern verbatim. Confidence is medium (0.75).
    RegexRedactor(
        "pii.network.ipv4",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
        ),
        confidence=0.75,
    ),

    # pii.network.ipv6
    # Taxonomy pattern verbatim: matches full 8-group uncompressed IPv6 only.
    # Abbreviated (::) forms are NOT matched (corpus rows updated to full form per T11.2).
    RegexRedactor(
        "pii.network.ipv6",
        re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"),
        confidence=0.95,
    ),

    # payment.card.pan
    # Taxonomy pattern verbatim with Luhn checksum validator.
    # The validator ensures only structurally valid PANs are redacted.
    RegexRedactor(
        "payment.card.pan",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        validator=lambda m: is_luhn_valid(m.group(0)),
        confidence=0.95,
    ),

    # payment.card.expiration DISABLED 2026-05-20.
    # The naive month-year regex matched ISO date substrings
    # such as the day portion of any 2026-NN-NN string, and any
    # casual US-style date. In a session that handles dates daily
    # this caused block-the-call on routine git logs and timestamps.
    # Re-enable only when wrapped in a ContextRedactor that requires
    # nearby card-context keywords (card, credit, debit, exp, cvv,
    # pan, merchant). See gz-beads gz-vrxja for tracking.
]
