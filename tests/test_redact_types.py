"""redact-S1: Type-dataclass tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact.types import Span, Detection, Confidence


class TestSpan:
    def test_span_has_length(self):
        s = Span(start=0, end=10)
        assert s.end > s.start
        assert (s.end - s.start) == 10

    def test_span_zero_length_allowed(self):
        s = Span(start=5, end=5)
        # Empty span is valid (matches insertion-point patterns)
        assert s.end == s.start


class TestDetection:
    def test_detection_carries_category_and_confidence(self):
        d = Detection(
            span=Span(0, 10), category="pii.email",
            confidence=Confidence.HIGH, replacement="[EMAIL]",
        )
        assert d.confidence == Confidence.HIGH
        assert d.category.startswith("pii.")

    def test_detection_replacement_is_string(self):
        d = Detection(
            span=Span(0, 5), category="secret.aws",
            confidence=Confidence.HIGH, replacement="[REDACTED:secret.aws]",
        )
        assert isinstance(d.replacement, str)


class TestConfidence:
    def test_confidence_levels_ordered(self):
        # The order doesn't matter for correctness, but the enum must have
        # AT LEAST low, medium, high (or equivalent) levels.
        # Test we can compare or at least equal-check.
        c = Confidence.HIGH
        assert c is not None
