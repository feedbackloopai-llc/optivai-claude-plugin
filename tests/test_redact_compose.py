"""redact-S2: Pipeline composition + overlap resolution tests."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact import compose, redact, RegexRedactor


class TestCompose:
    def test_empty_pipeline_passthrough(self):
        out = redact("untouched", compose([]))
        assert out == "untouched"

    def test_none_returns_none(self):
        out = redact(None, compose([]))
        assert out is None

    def test_single_redactor_applies(self):
        # Match the literal token "foo123" and replace with "[FOO]"
        r = RegexRedactor([(re.compile(r"foo\d+"), "test.foo", "[FOO]", 0.9)])
        out = redact("see foo123 here", compose([r]))
        assert "foo123" not in out
        assert "[FOO]" in out

    def test_two_non_overlapping_redactors_both_apply(self):
        r1 = RegexRedactor([(re.compile(r"foo\d+"), "test.foo", "[FOO]", 0.9)])
        r2 = RegexRedactor([(re.compile(r"bar\d+"), "test.bar", "[BAR]", 0.9)])
        out = redact("foo1 and bar2", compose([r1, r2]))
        assert "[FOO]" in out
        assert "[BAR]" in out


class TestOverlapResolution:
    def test_higher_confidence_wins_on_overlap(self):
        # Two regexes that match the SAME substring; higher-confidence one wins
        r1 = RegexRedactor([(re.compile(r"foo\d+"), "test.r1", "[R1]", 0.5)])
        r2 = RegexRedactor([(re.compile(r"foo123"), "test.r2", "[R2]", 0.9)])
        out = redact("see foo123 here", compose([r1, r2]))
        # The 0.9-confidence redactor should win
        assert "[R2]" in out
        assert "[R1]" not in out

    def test_clean_text_unchanged(self):
        r = RegexRedactor([(re.compile(r"foo\d+"), "test.foo", "[FOO]", 0.9)])
        out = redact("hello world", compose([r]))
        assert out == "hello world"
