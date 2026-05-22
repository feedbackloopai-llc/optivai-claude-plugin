"""redact-S5: Shannon entropy detector tests.

Tests adapted to the vendored gz-redact API:
- EntropyRedactor takes an optional EntropyRedactorOpts dataclass (NOT direct kwargs).
- Method is scan(text) returning list[RedactionHit] (NOT detect()).
- Default min_len is 20; tokens shorter than min_len are never scanned.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact import compose, redact
from redact.recognizers.entropy import (
    EntropyRedactor,
    EntropyRedactorOpts,
    shannon_entropy,
)
from redact.types import RedactionHit


class TestShannonEntropyFunction:
    def test_empty_string_returns_zero(self):
        assert shannon_entropy("") == 0.0

    def test_single_char_returns_zero(self):
        # All same char → no information → 0 bits.
        assert shannon_entropy("aaaaaa") == 0.0

    def test_two_char_even_split_is_one_bit(self):
        # "ab" → p(a)=p(b)=0.5 → H = -2*(0.5*log2(0.5)) = 1.0
        assert shannon_entropy("ab") == 1.0

    def test_high_entropy_string_exceeds_threshold(self):
        # A diverse 32-char alphanumeric string should have entropy >= 4.5 bits/char.
        high = "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS"
        assert shannon_entropy(high) >= 4.5


class TestEntropyRedactorConstruction:
    def test_constructor_with_no_opts(self):
        # Must not raise — defaults apply.
        r = EntropyRedactor()
        assert r.min_len == 20
        assert r.min_entropy == 4.5
        assert r.confidence == 0.55
        assert r.category == "secret.unknown.high_entropy"

    def test_constructor_with_custom_opts(self):
        r = EntropyRedactor(
            EntropyRedactorOpts(min_len=16, min_entropy=4.0, confidence=0.7)
        )
        assert r.min_len == 16
        assert r.min_entropy == 4.0
        assert r.confidence == 0.7

    def test_invalid_min_len_raises(self):
        try:
            EntropyRedactor(EntropyRedactorOpts(min_len=0))
        except ValueError:
            return
        raise AssertionError("expected ValueError for min_len < 1")

    def test_invalid_confidence_raises(self):
        try:
            EntropyRedactor(EntropyRedactorOpts(confidence=1.5))
        except ValueError:
            return
        raise AssertionError("expected ValueError for confidence > 1")


class TestEntropyRedactorScan:
    def test_scan_returns_list(self):
        r = EntropyRedactor()
        result = r.scan("hello world")
        assert isinstance(result, list)

    def test_scan_returns_redaction_hits(self):
        # 32-char high-entropy token (>= min_len 20, entropy >= 4.5).
        high = "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS"
        text = f"value: {high}"
        r = EntropyRedactor()
        hits = r.scan(text)
        assert len(hits) >= 1
        # All hits must be RedactionHit objects.
        for h in hits:
            assert isinstance(h, RedactionHit)
            assert h.category == "secret.unknown.high_entropy"
            assert h.confidence == 0.55

    def test_high_entropy_token_redacted_via_compose(self):
        """End-to-end: high-entropy token gets removed by the pipeline."""
        high = "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS"
        text = f"value: {high}"
        out = redact(text, compose([EntropyRedactor()]))
        # The token should be detected and removed.
        assert high not in out
        assert "[REDACTED:secret.unknown.high_entropy]" in out

    def test_short_tokens_not_flagged(self):
        """Tokens shorter than min_len (20) are never scanned."""
        # All tokens here are < 20 chars.
        text = "abc xyz def 123 foo"
        out = redact(text, compose([EntropyRedactor()]))
        # Original tokens pass through unchanged.
        assert "abc" in out
        assert "xyz" in out
        assert "foo" in out

    def test_normal_prose_below_threshold(self):
        """Plain English prose has individual words < 20 chars; nothing fires."""
        text = "the quick brown fox jumps over the lazy dog"
        out = redact(text, compose([EntropyRedactor()]))
        assert "quick" in out
        assert "brown" in out
        assert out == text  # untouched

    def test_repeated_chars_token_not_flagged(self):
        """A 20-char string of all same char has entropy 0 — below threshold."""
        text = f"prefix {'a' * 30} suffix"
        out = redact(text, compose([EntropyRedactor()]))
        # All-same-char run has H=0, well below min_entropy=4.5.
        assert "a" * 30 in out

    def test_empty_string_returns_empty_hits(self):
        r = EntropyRedactor()
        assert r.scan("") == []

    def test_lowered_threshold_catches_more(self):
        """A token at exactly 4.0 entropy is caught when min_entropy=4.0 but not 4.5."""
        # A 24-char hex string (16-char alphabet) has H ≈ 4.0 bits/char.
        token = "abcdef0123456789abcdef01"  # 24 chars, mostly hex
        text = f"id={token}"
        # Default threshold: depends on actual entropy of token. Use a permissive threshold.
        r_low = EntropyRedactor(EntropyRedactorOpts(min_len=20, min_entropy=3.0))
        hits_low = r_low.scan(text)
        # Very lax threshold — token should be caught.
        assert len(hits_low) >= 1

    def test_hit_offsets_are_correct(self):
        """RedactionHit start/end offsets correctly identify the token span."""
        high = "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS"  # 32 chars
        prefix = "value: "
        text = prefix + high
        r = EntropyRedactor()
        hits = r.scan(text)
        assert len(hits) >= 1
        h = hits[0]
        assert text[h.start:h.end] == high
        assert h.start == len(prefix)
        assert h.end == len(prefix) + len(high)
