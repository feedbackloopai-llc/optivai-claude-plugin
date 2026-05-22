"""redact-S6: Context-aware confidence upgrade tests.

Tests adapted to the vendored gz-redact API:
- ContextRedactor takes keyword-only args (inner=, keywords=, radius=, ...).
- Inner redactor is anything implementing the Redactor protocol (scan()).
- Default radius is 10 chars; default upgraded_confidence is 0.85.
- suppress_low_confidence is optional (default None); when set, hits below
  the threshold with no keyword nearby are dropped.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact import compose, redact
from redact.recognizers.context import ContextRedactor
from redact.recognizers.entropy import EntropyRedactor, EntropyRedactorOpts
from redact.types import RedactionHit, Redactor


# ---------------------------------------------------------------------------
# A tiny test-only inner redactor: emits one fixed hit at known offsets.
# Lets us isolate ContextRedactor logic from EntropyRedactor specifics.
# ---------------------------------------------------------------------------

class FixedRedactor:
    """Test-only Redactor that emits one hit at a fixed position and confidence."""

    def __init__(self, start: int, end: int, confidence: float = 0.55,
                 category: str = "test.fixed",
                 replacement: str = "[REDACTED:test.fixed]") -> None:
        self.start = start
        self.end = end
        self.confidence = confidence
        self.category = category
        self.replacement = replacement

    def scan(self, text: str) -> list[RedactionHit]:
        if self.end > len(text):
            return []
        return [RedactionHit(
            start=self.start,
            end=self.end,
            category=self.category,
            confidence=self.confidence,
            replacement=self.replacement,
        )]


class TestContextRedactorConstruction:
    def test_constructor_accepts_inner_and_keywords(self):
        # Must not raise.
        ContextRedactor(
            inner=EntropyRedactor(),
            keywords=["password", "secret"],
            radius=15,
            upgraded_confidence=0.85,
            suppress_low_confidence=0.6,
        )

    def test_constructor_rejects_empty_keywords(self):
        try:
            ContextRedactor(inner=EntropyRedactor(), keywords=[])
        except ValueError:
            return
        raise AssertionError("expected ValueError for empty keywords list")

    def test_constructor_rejects_negative_radius(self):
        try:
            ContextRedactor(
                inner=EntropyRedactor(),
                keywords=["secret"],
                radius=-1,
            )
        except ValueError:
            return
        raise AssertionError("expected ValueError for radius < 0")

    def test_constructor_rejects_bad_upgraded_confidence(self):
        try:
            ContextRedactor(
                inner=EntropyRedactor(),
                keywords=["secret"],
                upgraded_confidence=1.5,
            )
        except ValueError:
            return
        raise AssertionError("expected ValueError for upgraded_confidence > 1")


class TestKeywordUpgrade:
    def test_keyword_nearby_upgrades_confidence(self):
        """A hit with 'password' within radius gets confidence upgraded."""
        text = "user password: TOKEN"  # 'password' adjacent to TOKEN
        # FixedRedactor flags positions for "TOKEN" (15..20).
        inner = FixedRedactor(start=15, end=20, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password"],
            radius=15,
            upgraded_confidence=0.85,
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        # Confidence upgraded.
        assert hits[0].confidence == 0.85
        # Span and replacement preserved.
        assert hits[0].start == 15
        assert hits[0].end == 20

    def test_high_entropy_near_password_keyword_redacted_via_pipeline(self):
        """End-to-end: entropy hit near 'password' becomes high-confidence."""
        # 32-char high-entropy token + 'password' keyword nearby.
        text = "the user password is xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS"
        pipeline = compose([
            ContextRedactor(
                inner=EntropyRedactor(),
                keywords=["password", "secret", "api_key"],
                radius=20,
                upgraded_confidence=0.85,
                suppress_low_confidence=0.6,
            ),
        ])
        out = redact(text, pipeline)
        # Token redacted because nearby keyword upgraded confidence above
        # suppress_low_confidence threshold.
        assert "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS" not in out

    def test_keyword_is_case_insensitive(self):
        """Keywords match case-insensitively."""
        text = "user PASSWORD: TOKEN"
        inner = FixedRedactor(start=15, end=20, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password"],  # lowercase
            radius=15,
            upgraded_confidence=0.85,
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        assert hits[0].confidence == 0.85


class TestKeywordSuppression:
    def test_low_confidence_without_keyword_suppressed(self):
        """Hit with confidence below suppress_low_confidence threshold and no
        keyword nearby is dropped."""
        text = "transaction id TOKEN recorded later"
        inner = FixedRedactor(start=15, end=20, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password", "secret"],
            radius=10,
            upgraded_confidence=0.85,
            suppress_low_confidence=0.6,  # 0.55 < 0.6 → suppress
        )
        hits = ctx.scan(text)
        assert hits == []  # Dropped.

    def test_high_entropy_without_keyword_suppressed_via_pipeline(self):
        """End-to-end: high-entropy in non-secret context is suppressed."""
        text = "transaction id xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS recorded"
        pipeline = compose([
            ContextRedactor(
                inner=EntropyRedactor(),
                keywords=["password", "secret", "api_key"],
                radius=20,
                upgraded_confidence=0.85,
                suppress_low_confidence=0.6,
            ),
        ])
        out = redact(text, pipeline)
        # Token preserved: no keyword nearby + base confidence < suppress threshold.
        assert "xZ7mQ9pK3rT2nL5wH8jB4vY6cF1aN0dS" in out

    def test_no_suppression_without_threshold(self):
        """When suppress_low_confidence is None (default), low-confidence
        hits without keywords pass through unchanged."""
        text = "transaction id TOKEN recorded later"
        inner = FixedRedactor(start=15, end=20, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password", "secret"],
            radius=10,
            upgraded_confidence=0.85,
            # No suppress_low_confidence (default None).
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        assert hits[0].confidence == 0.55  # unchanged

    def test_high_confidence_without_keyword_preserved(self):
        """Hit with confidence >= suppress_low_confidence is kept even if no
        keyword nearby."""
        text = "transaction id TOKEN recorded later"
        inner = FixedRedactor(start=15, end=20, confidence=0.95)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password", "secret"],
            radius=10,
            upgraded_confidence=0.85,
            suppress_low_confidence=0.6,
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        # Kept; confidence unchanged (no keyword found, not below threshold).
        assert hits[0].confidence == 0.95


class TestRadiusBehavior:
    def test_keyword_within_radius_upgrades(self):
        """A keyword exactly at the radius boundary upgrades."""
        # 'pw' at offset 0, hit at offset 5..10. Radius 5 covers it.
        text = "pw   TOKEN"
        inner = FixedRedactor(start=5, end=10, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["pw"],
            radius=5,
            upgraded_confidence=0.85,
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        assert hits[0].confidence == 0.85

    def test_keyword_far_from_hit_does_not_upgrade(self):
        """A keyword far outside the radius window does NOT trigger upgrade."""
        # Construct: keyword 'password' at offset 0, hit very far away.
        # Use a long filler so the keyword is well beyond the radius window.
        text = "password" + ("." * 200) + "TOKEN_DELTA"
        # hit occupies offset 208..219 ("TOKEN_DELTA" is 11 chars).
        hit_start = len("password") + 200
        hit_end = hit_start + len("TOKEN_DELTA")
        assert text[hit_start:hit_end] == "TOKEN_DELTA"

        inner = FixedRedactor(start=hit_start, end=hit_end, confidence=0.55)
        ctx = ContextRedactor(
            inner=inner,
            keywords=["password"],
            radius=10,  # 10-char window — far less than the 200-char gap
            upgraded_confidence=0.85,
            suppress_low_confidence=None,  # don't suppress so we can inspect
        )
        hits = ctx.scan(text)
        assert len(hits) == 1
        # Confidence unchanged: keyword too far away.
        assert hits[0].confidence == 0.55


class TestProtocolConformance:
    def test_context_redactor_is_a_redactor(self):
        """ContextRedactor satisfies the Redactor protocol."""
        ctx = ContextRedactor(
            inner=EntropyRedactor(),
            keywords=["password"],
        )
        assert isinstance(ctx, Redactor)

    def test_empty_text_returns_empty_hits(self):
        ctx = ContextRedactor(
            inner=EntropyRedactor(),
            keywords=["password"],
        )
        assert ctx.scan("") == []
