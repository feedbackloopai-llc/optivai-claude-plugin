#!/usr/bin/env python3
"""fblai-1ybnr STEP B — hooks writers use composed redaction pipeline.

Tests:
  1. memory_writer.redact_secrets catches the same corpus as the open_brain pipeline:
       - AWS access key
       - Anthropic key (sk-ant-api03-...)
       - Email address
       - US SSN
       - Compressed IPv6 (::1)
  2. beads_writer.redact_secrets catches the same corpus.
  3. memory_writer fallback path: when the import fails, WARNING is emitted
     to stderr exactly once.
  4. beads_writer fallback path: same.

Run: cd scripts && python3 -m pytest hooks/tests/test_writers_redaction.py -v
"""

import importlib
import io
import sys
import os
import types
import unittest.mock as mock

import pytest

# Ensure scripts/ is on the path.
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Shared fixture corpus
# ---------------------------------------------------------------------------

CORPUS = {
    "aws_key":        ("AKIAIOSFODNN7EXAMPLE", "AWS access key"),
    "anthropic_key":  ("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-AAAAAAAAAAAAAAAAAAAAAA-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "Anthropic API key"),
    "email":          ("user@example.com", "email address"),
    "ssn":            ("123-45-6789", "SSN"),
    "ipv6_compressed": ("::1", "compressed IPv6"),
}


# ---------------------------------------------------------------------------
# Helper: import a fresh (not cached) module using importlib
# ---------------------------------------------------------------------------

def _fresh_import(module_name: str):
    """Import module_name with a clean sys.modules entry."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# Tests for memory_writer
# ---------------------------------------------------------------------------

class TestMemoryWriterRedaction:
    """memory_writer.redact_secrets / redact_dict cover full corpus."""

    def setup_method(self):
        # Fresh import to avoid state leakage between tests.
        if "hooks.memory_writer" in sys.modules:
            del sys.modules["hooks.memory_writer"]
        if "memory_writer" in sys.modules:
            del sys.modules["memory_writer"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import memory_writer as mw
        self.mw = mw

    def test_aws_key_redacted(self):
        raw = f"the prod key is {CORPUS['aws_key'][0]}"
        result = self.mw.redact_secrets(raw)
        assert CORPUS["aws_key"][0] not in result, (
            f"AWS key not redacted: {result!r}"
        )

    def test_anthropic_key_redacted(self):
        raw = f"export ANT_KEY={CORPUS['anthropic_key'][0]}"
        result = self.mw.redact_secrets(raw)
        assert CORPUS["anthropic_key"][0] not in result, (
            f"Anthropic key not redacted: {result!r}"
        )

    def test_email_redacted(self):
        raw = f"send to {CORPUS['email'][0]} please"
        result = self.mw.redact_secrets(raw)
        assert CORPUS["email"][0] not in result, (
            f"Email not redacted: {result!r}"
        )

    def test_ssn_redacted(self):
        raw = f"SSN is {CORPUS['ssn'][0]}"
        result = self.mw.redact_secrets(raw)
        assert CORPUS["ssn"][0] not in result, (
            f"SSN not redacted: {result!r}"
        )

    def test_compressed_ipv6_redacted(self):
        raw = f"loopback is {CORPUS['ipv6_compressed'][0]}"
        result = self.mw.redact_secrets(raw)
        assert CORPUS["ipv6_compressed"][0] not in result, (
            f"Compressed IPv6 not redacted: {result!r}"
        )

    def test_redact_dict_catches_secrets_in_values(self):
        data = {
            "key": CORPUS["aws_key"][0],
            "email": CORPUS["email"][0],
            "nested": {"ssn": CORPUS["ssn"][0]},
        }
        result = self.mw.redact_dict(data)
        assert CORPUS["aws_key"][0] not in result.get("key", ""), "AWS key in dict not redacted"
        assert CORPUS["email"][0] not in result.get("email", ""), "Email in dict not redacted"
        nested_ssn = result.get("nested", {}).get("ssn", "")
        assert CORPUS["ssn"][0] not in nested_ssn, "SSN in nested dict not redacted"

    def test_none_input_returns_none(self):
        assert self.mw.redact_secrets(None) is None or self.mw.redact_secrets(None) == None  # noqa: E711

    def test_clean_text_passes_through(self):
        clean = "The quick brown fox jumps over the lazy dog."
        result = self.mw.redact_secrets(clean)
        assert result == clean, f"Clean text was mangled: {result!r}"


# ---------------------------------------------------------------------------
# Tests for beads_writer
# ---------------------------------------------------------------------------

class TestBeadsWriterRedaction:
    """beads_writer.redact_secrets / redact_dict cover full corpus."""

    def setup_method(self):
        if "hooks.beads_writer" in sys.modules:
            del sys.modules["hooks.beads_writer"]
        if "beads_writer" in sys.modules:
            del sys.modules["beads_writer"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import beads_writer as bw
        self.bw = bw

    def test_aws_key_redacted(self):
        raw = f"prod bucket key={CORPUS['aws_key'][0]}"
        result = self.bw.redact_secrets(raw)
        assert CORPUS["aws_key"][0] not in result, f"AWS key not redacted in beads_writer: {result!r}"

    def test_anthropic_key_redacted(self):
        raw = f"key: {CORPUS['anthropic_key'][0]}"
        result = self.bw.redact_secrets(raw)
        assert CORPUS["anthropic_key"][0] not in result, (
            f"Anthropic key not redacted in beads_writer: {result!r}"
        )

    def test_email_redacted(self):
        raw = f"notify {CORPUS['email'][0]}"
        result = self.bw.redact_secrets(raw)
        assert CORPUS["email"][0] not in result, f"Email not redacted in beads_writer: {result!r}"

    def test_ssn_redacted(self):
        raw = f"ID={CORPUS['ssn'][0]}"
        result = self.bw.redact_secrets(raw)
        assert CORPUS["ssn"][0] not in result, f"SSN not redacted in beads_writer: {result!r}"

    def test_compressed_ipv6_redacted(self):
        raw = f"bind to {CORPUS['ipv6_compressed'][0]} port 443"
        result = self.bw.redact_secrets(raw)
        assert CORPUS["ipv6_compressed"][0] not in result, (
            f"Compressed IPv6 not redacted in beads_writer: {result!r}"
        )

    def test_clean_text_passes_through(self):
        clean = "Created bead for code review"
        result = self.bw.redact_secrets(clean)
        assert result == clean, f"Clean bead title was mangled: {result!r}"


# ---------------------------------------------------------------------------
# Fallback path: import failure emits WARNING, does not silently swallow
# ---------------------------------------------------------------------------

def _force_import_failure_and_capture_stderr(module_path: str):
    """
    Import module_path after patching 'redact.default_pipeline' out of sys.modules
    so the import inside _bootstrap_redact_pipeline() raises ImportError.
    Captures stderr and returns (module, stderr_text).
    """
    # Remove any cached copies so we get a fresh module.
    for key in list(sys.modules.keys()):
        if "memory_writer" in key or "beads_writer" in key:
            del sys.modules[key]

    captured = io.StringIO()

    # We make 'redact.default_pipeline' unimportable by temporarily replacing
    # the 'redact' package in sys.modules with a stub that raises on attribute access.
    original_redact = sys.modules.get("redact")
    original_default_pipeline = sys.modules.get("redact.default_pipeline")

    # Stub module that raises ImportError when default_pipeline is accessed.
    stub_redact = types.ModuleType("redact")

    class _FailingDefaultPipeline:
        def __getattr__(self, name):
            raise ImportError("Forced import failure for test")

    stub_default_pipeline = _FailingDefaultPipeline()

    sys.modules["redact"] = stub_redact
    sys.modules["redact.default_pipeline"] = stub_default_pipeline  # type: ignore[assignment]

    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        # Import the module fresh — _bootstrap_redact_pipeline will fail.
        if module_path in sys.modules:
            del sys.modules[module_path]
        mod = importlib.import_module(module_path)
        # Trigger the fallback warn by calling redact_secrets.
        mod.redact_secrets("test input with AKIAIOSFODNN7EXAMPLE key")
    finally:
        sys.stderr = old_stderr
        # Restore sys.modules.
        if original_redact is None:
            sys.modules.pop("redact", None)
        else:
            sys.modules["redact"] = original_redact
        if original_default_pipeline is None:
            sys.modules.pop("redact.default_pipeline", None)
        else:
            sys.modules["redact.default_pipeline"] = original_default_pipeline
        # Clean up the imported module so it doesn't pollute other tests.
        for key in list(sys.modules.keys()):
            if "memory_writer" in key or "beads_writer" in key:
                del sys.modules[key]

    return mod, captured.getvalue()


def test_memory_writer_fallback_emits_warning():
    """When redact pipeline is unavailable, memory_writer warns loudly on stderr."""
    # Ensure hooks/ directory is on path.
    hooks_dir = os.path.join(os.path.dirname(__file__), "..")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    _, stderr_output = _force_import_failure_and_capture_stderr("memory_writer")
    assert "WARNING" in stderr_output, (
        f"memory_writer fallback did not emit WARNING to stderr. Got: {stderr_output!r}"
    )
    assert "redaction" in stderr_output.lower() or "DISABLED" in stderr_output, (
        f"memory_writer WARNING doesn't mention redaction: {stderr_output!r}"
    )


def test_beads_writer_fallback_emits_warning():
    """When redact pipeline is unavailable, beads_writer warns loudly on stderr."""
    hooks_dir = os.path.join(os.path.dirname(__file__), "..")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    _, stderr_output = _force_import_failure_and_capture_stderr("beads_writer")
    assert "WARNING" in stderr_output, (
        f"beads_writer fallback did not emit WARNING to stderr. Got: {stderr_output!r}"
    )
    assert "redaction" in stderr_output.lower() or "DISABLED" in stderr_output, (
        f"beads_writer WARNING doesn't mention redaction: {stderr_output!r}"
    )


def test_memory_writer_fallback_warns_only_once():
    """Fallback warning is emitted once even when called multiple times."""
    hooks_dir = os.path.join(os.path.dirname(__file__), "..")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    for key in list(sys.modules.keys()):
        if "memory_writer" in key or "beads_writer" in key:
            del sys.modules[key]

    captured = io.StringIO()
    original_redact = sys.modules.get("redact")
    original_default_pipeline = sys.modules.get("redact.default_pipeline")
    stub_redact = types.ModuleType("redact")

    class _FailingDefaultPipeline:
        def __getattr__(self, name):
            raise ImportError("Forced import failure")

    sys.modules["redact"] = stub_redact
    sys.modules["redact.default_pipeline"] = _FailingDefaultPipeline()  # type: ignore[assignment]

    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        if "memory_writer" in sys.modules:
            del sys.modules["memory_writer"]
        import memory_writer as mw
        mw.redact_secrets("text1")
        mw.redact_secrets("text2")
        mw.redact_secrets("text3")
    finally:
        sys.stderr = old_stderr
        if original_redact is None:
            sys.modules.pop("redact", None)
        else:
            sys.modules["redact"] = original_redact
        if original_default_pipeline is None:
            sys.modules.pop("redact.default_pipeline", None)
        else:
            sys.modules["redact.default_pipeline"] = original_default_pipeline
        for key in list(sys.modules.keys()):
            if "memory_writer" in key:
                del sys.modules[key]

    warning_count = captured.getvalue().count("WARNING")
    assert warning_count == 1, (
        f"Expected exactly 1 WARNING, got {warning_count}. Output: {captured.getvalue()!r}"
    )
