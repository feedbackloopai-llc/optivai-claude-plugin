#!/usr/bin/env python3
"""Tests for compressed IPv6 recognizer — fblai-1ybnr STEP A.

Verifies that the composed pii_redactors pipeline (or direct pii recognizer
set) catches compressed IPv6 addresses while avoiding catastrophic false
positives on common code patterns containing `::`.

Tradeoff documented in pii.py:
  - The compressed-IPv6 recognizer targets well-formed compressed notation:
    addresses with at least one hex group + `::` + optional continuation, or
    addresses that end / begin with `::`.
  - Known residual false positive: C++ qualified names such as
    `namespace::Class::method` where all segments look like valid hex (e.g.
    `abc::def`) may be matched. Mitigated by requiring at least two consecutive
    hex groups ADJACENT to the `::`, or a leading/trailing lone `::`.
  - Known residual false negative: a bare `::` with no surrounding hex groups
    is not matched (this is the unspecified all-zeros address shorthand — it
    would have too many code false positives to match safely).

Run: cd scripts && python3 -m pytest tests/test_redact_ipv6.py -v
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from redact import compose, redact as redact_text, pii_redactors  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

_PIPELINE = compose(pii_redactors)


def _redact(text: str) -> str:
    return redact_text(text, _PIPELINE)


# ── STEP A tests: compressed IPv6 caught ─────────────────────────────────────


def test_loopback_compressed_ipv6():
    """::1 (IPv6 loopback) must be redacted."""
    result = _redact("connecting to ::1 on port 8080")
    assert "::1" not in result, f"::1 not redacted in: {result!r}"
    assert "[REDACTED" in result or "REDACTED" in result


def test_fe80_link_local_compressed():
    """fe80::1 (link-local) must be redacted."""
    result = _redact("interface fe80::1 is up")
    assert "fe80::1" not in result, f"fe80::1 not redacted in: {result!r}"


def test_2001_db8_documentation_prefix():
    """2001:db8::1 (documentation prefix) must be redacted."""
    result = _redact("The server listens on 2001:db8::1")
    assert "2001:db8::1" not in result, f"2001:db8::1 not redacted in: {result!r}"


def test_compressed_mid_double_colon():
    """2001:db8::85a3::8a2e::1 (multiple :: — technically malformed but contains compressed pattern)."""
    # The pattern should catch this as a best-effort match on the leading compressed segment.
    text = "host 2001:db8::85a3:0:0:1 is unreachable"
    result = _redact(text)
    # The compressed address 2001:db8::85a3:0:0:1 must not survive
    assert "2001:db8::85a3:0:0:1" not in result, f"compressed addr not redacted in: {result!r}"


def test_ipv4_mapped_ipv6():
    """::ffff:192.168.1.1 (IPv4-mapped IPv6) must be redacted."""
    result = _redact("mapped address is ::ffff:192.168.1.1 in the logs")
    assert "::ffff:192.168.1.1" not in result, f"IPv4-mapped IPv6 not redacted in: {result!r}"


def test_full_uncompressed_ipv6_still_caught():
    """Full 8-group form must still be caught (regression guard)."""
    addr = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
    result = _redact(f"host {addr}")
    assert addr not in result, f"Full IPv6 not redacted in: {result!r}"


# ── FALSE POSITIVE GUARD: common code patterns with :: ──────────────────────


def test_cpp_scope_operator_not_redacted():
    """C++ scope resolution ::std::vector must NOT be redacted.

    `std::vector` has no hex digits — the recognizer must not fire on
    purely alphabetic (non-hex) qualified names.
    """
    text = "::std::vector<int> items = ::std::vector<int>();"
    result = _redact(text)
    # The C++ qualified name must survive
    assert "std" in result, f"C++ scope operator mangled: {result!r}"


def test_python_double_star_not_redacted():
    """foo::bar (if non-hex names) must not be redacted."""
    text = "Call foo::bar() to initialise the module"
    result = _redact(text)
    # foo and bar are all-alpha — not hex — so no match expected
    assert "foo" in result, f"Non-hex scope operator mangled: {result!r}"


def test_prose_double_colon_not_redacted():
    """The sentence 'items :: sorted' must not be redacted."""
    text = "Use :: to denote a separator in config files"
    result = _redact(text)
    assert "::" in result, f"Prose double-colon mangled: {result!r}"


# ── REDOS GUARD ──────────────────────────────────────────────────────────────


def test_no_redos_on_pathological_input():
    """Pathological string of colons and hex chars must not hang.

    A catastrophically backtracking regex on a 1000-char input of `a:` repeated
    would take seconds. Assert completion within 2 seconds.
    """
    pathological = ("a:" * 500) + "x"
    start = time.monotonic()
    _redact(pathological)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, (
        f"ReDoS: redact took {elapsed:.2f}s on pathological input (limit=2s)"
    )


def test_no_redos_on_double_colon_repeat():
    """String of '::' repeated many times must not hang."""
    pathological = "::" * 200
    start = time.monotonic()
    _redact(pathological)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, (
        f"ReDoS: redact took {elapsed:.2f}s on repeated '::' (limit=2s)"
    )
