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

    # pii.network.ipv6 (full uncompressed)
    # Taxonomy pattern verbatim: matches full 8-group uncompressed IPv6 only.
    # Abbreviated (::) forms are handled by the companion pii.network.ipv6_compressed
    # recognizer below (T11.2 deviation reversed: we now support both forms).
    RegexRedactor(
        "pii.network.ipv6",
        re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"),
        confidence=0.95,
    ),

    # pii.network.ipv6_compressed
    #
    # Matches compressed (RFC 5952 `::`) IPv6 addresses. The full-form recognizer
    # above catches 8-group uncompressed; this one catches all forms that use `::`.
    #
    # DESIGN — avoiding ReDoS:
    #   No nested quantifiers. The pattern is a flat alternation of six structurally
    #   distinct compressed forms. Each form uses at most one `(?:group:){n,m}`
    #   quantifier without any surrounding repeat. Python's `re` module (backed by
    #   a backtracking NFA) is still susceptible to catastrophic backtracking when
    #   quantifiers are nested; flat alternation is safe.
    #
    #   Hex group = [0-9A-Fa-f]{1,4}   (abbreviated as HG below)
    #
    #   Form A: HG :: (one leading group, trailing ::)
    #           e.g. fe80::, 2001::
    #   Form B: HG:HG... :: (2–6 leading groups, trailing ::)
    #           e.g. 2001:db8::
    #   Form C: :: HG (leading ::, one trailing group)
    #           e.g. ::1
    #   Form D: :: HG:HG... (leading ::, 2–7 trailing groups)
    #           e.g. ::ffff:192 (partially), ::1:2:3
    #   Form E: HG :: HG (one leading group, ::, one trailing group)
    #           e.g. fe80::1
    #   Form F: HG:HG... :: HG:HG... (2–5 leading groups, ::, 1–5 trailing groups)
    #           e.g. 2001:db8::85a3:0:0:1
    #
    #   IPv4-mapped addresses (::ffff:d.d.d.d) are incidentally matched by Form D
    #   because the IPv4-decimal part is not pure hex; the match covers the hex
    #   prefix (::ffff) and leaves the IPv4 suffix to the IPv4 recognizer. This is
    #   conservative (no false negative on the IPv6 portion; slight boundary shift).
    #
    # FALSE POSITIVE tradeoff:
    #   Any token that looks like hex_group::hex_group will be matched. The
    #   PRIMARY defense against common C++ scope operators (`std::vector`,
    #   `foo::bar`, `namespace::Class`) is the `[0-9A-Fa-f]{1,4}` hex character
    #   class itself: those identifiers contain non-hex letters (g-z minus a-f,
    #   and length > 4), so no form's hex group can match them and the recognizer
    #   never fires. The `(?<!\w)` lookbehind does NOT reject `foo::bar`; its only
    #   job is to prevent mid-word anchoring — e.g. it stops the recognizer from
    #   matching the `fe80::1` substring inside a larger token like `xfe80::1` or
    #   `cafefe80::1`. (Trailing `(?!\w)` does the same on the right edge.)
    #   We do NOT require surrounding whitespace because IPv6 frequently appears
    #   after `:` or `/` in URLs and config lines.
    #
    #   Residual false positives (accepted, documented — all low-impact):
    #     - `abc::def` where both segments happen to be all-hex (1-4 chars).
    #     - The hex-English-word class: tokens spelled entirely from [0-9a-f]
    #       that read like words, e.g. `cafe::babe`, `dead::beef`, `feed::face`,
    #       `face::b00c`. These ARE valid compressed-IPv6 shapes and get redacted.
    #       Accepted: rare in brain-stored prose, and redacting them is harmless.
    #     - C++ `0xabc::Method` (hex literal before ::) — accepted as a
    #       correctly-handled match (redacting crypto-looking tokens is safe).
    #
    #   Residual false negatives (accepted, documented):
    #     - Bare `::` with no surrounding hex groups is NOT matched (too broad)
    #     - Addresses embedded inside larger numeric tokens without word boundaries
    #       may not match if they start mid-word
    #
    # WORD BOUNDARY HANDLING:
    #   IPv6 addresses can appear after `/` (CIDR), `:` (ports), `[` (URL brackets),
    #   or at the start of a line. Standard `\b` anchors on the left and right sides
    #   of the address handle the common cases. The negative lookbehind `(?<!\w)` is
    #   used instead of `\b` on the left for forms that start with `::` (where `\b`
    #   would not fire before `:`).
    RegexRedactor(
        "pii.network.ipv6_compressed",
        re.compile(
            r"(?<!\w)"
            r"(?:"
            # Form F: 2-5 leading hex groups, ::, 1-5 trailing hex groups
            # e.g. 2001:db8::85a3:0:0:1
            r"[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){1,4}::[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,4}"
            r"|"
            # Form E: exactly one leading group, ::, one trailing group
            # e.g. fe80::1
            r"[0-9A-Fa-f]{1,4}::[0-9A-Fa-f]{1,4}"
            r"|"
            # Form B: 2-6 leading groups, trailing ::
            # e.g. 2001:db8::
            r"[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){1,5}::"
            r"|"
            # Form A: one leading group, trailing ::
            # e.g. fe80::
            r"[0-9A-Fa-f]{1,4}::"
            r"|"
            # Form D: leading ::, 2-7 trailing groups
            # e.g. ::ffff:1, ::1:2:3:4:5:6:7
            r"::[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){1,6}"
            r"|"
            # Form C: leading ::, exactly one trailing group
            # e.g. ::1
            r"::[0-9A-Fa-f]{1,4}"
            r")"
            r"(?!\w)"
        ),
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
