"""redact-S8: L3 ingest redaction in brain_hook.py auto-capture.

The fire-capture path in `scripts/hooks/brain_hook.py` builds a JSON payload
with the user's raw text and pipes it via subprocess to `open_brain.py
--from-pi`. These tests assert:

  1. Text without secrets passes through unchanged (no false positives).
  2. AWS keys are redacted BEFORE the payload is written to the subprocess
     stdin.
  3. Emails are redacted BEFORE the payload is written.
  4. If the redactor itself raises (monkey-patched broken redactor), the
     capture still proceeds with the ORIGINAL text — fail-open contract.

The entry point we monkeypatch is `subprocess.Popen` (used inside
`_fire_capture`) — we capture what's written to stdin and assert on the
JSON payload's `text` field.
"""
import json
import os
import sys
from io import BytesIO

import pytest

# Make scripts/hooks importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks"))


class _FakeStdin:
    """In-memory replacement for subprocess.Popen().stdin."""

    def __init__(self):
        self.buffer = BytesIO()

    def write(self, data):
        self.buffer.write(data)

    def close(self):
        pass


class _FakePopen:
    """Drop-in for subprocess.Popen that captures stdin payloads."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stdin = _FakeStdin()
        _FakePopen.instances.append(self)

    # Some callers may try to wait/poll/terminate — make those no-ops.
    def wait(self, *_a, **_k):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass


@pytest.fixture
def fake_popen(monkeypatch):
    """Patch subprocess.Popen in brain_hook to capture payload stdin."""
    import brain_hook

    _FakePopen.instances = []
    monkeypatch.setattr(brain_hook.subprocess, "Popen", _FakePopen)
    # Ensure BRAIN_SCRIPT existence check passes (it should exist in repo, but
    # be defensive against test invocation cwd).
    if not brain_hook.BRAIN_SCRIPT.exists():
        # Point at any file that does exist so the early-return guard is skipped
        from pathlib import Path
        monkeypatch.setattr(
            brain_hook, "BRAIN_SCRIPT", Path(__file__).resolve()
        )
    return _FakePopen


def _extract_payload_text(popen_instance):
    """Read the JSON payload from FakePopen stdin buffer and return text."""
    raw = popen_instance.stdin.buffer.getvalue().decode("utf-8")
    payload = json.loads(raw)
    return payload.get("text", "")


class TestL3IngestRedaction:
    def test_clean_text_passes_through_unchanged(self, fake_popen):
        """No-secret text must NOT be mutated by the redactor."""
        import brain_hook

        clean = "I decided to use ROW_NUMBER for the dedup logic this week"
        brain_hook._fire_capture(
            text=clean,
            source="hook-test",
            session_id="s-1",
            project="testproj",
        )
        assert len(_FakePopen.instances) == 1
        captured = _extract_payload_text(_FakePopen.instances[0])
        assert captured == clean

    def test_aws_key_redacted_before_capture(self, fake_popen):
        """An AWS access key in text must be redacted before the subprocess
        stdin write happens."""
        import brain_hook

        text = (
            "I decided to rotate the AWS key AKIAIOSFODNN7EXAMPLE today; "
            "next time we should use IAM roles instead."
        )
        brain_hook._fire_capture(
            text=text,
            source="hook-test",
            session_id="s-1",
            project="testproj",
        )
        assert len(_FakePopen.instances) == 1
        captured = _extract_payload_text(_FakePopen.instances[0])
        assert "AKIAIOSFODNN7EXAMPLE" not in captured, (
            f"AWS key leaked into capture payload: {captured!r}"
        )
        # The original semantic content should still be present
        assert "decided" in captured.lower()

    def test_email_redacted_before_capture(self, fake_popen):
        """A bare email must be redacted before the payload is written."""
        import brain_hook

        text = (
            "Meeting note: talked to alice@example.com about the migration "
            "and we decided to ship Tuesday."
        )
        brain_hook._fire_capture(
            text=text,
            source="hook-test",
            session_id="s-1",
            project="testproj",
        )
        assert len(_FakePopen.instances) == 1
        captured = _extract_payload_text(_FakePopen.instances[0])
        assert "alice@example.com" not in captured, (
            f"Email leaked into capture payload: {captured!r}"
        )
        assert "Tuesday" in captured


class TestL3FailOpen:
    def test_redact_failure_does_not_block_capture(self, fake_popen, monkeypatch):
        """If `_redact_pii` raises, the auto-capture must still proceed with
        the ORIGINAL text. This is the fail-open contract: better to capture
        an un-redacted thought than to lose the memory entirely.
        """
        import brain_hook

        def broken_redactor(_text):
            raise RuntimeError("simulated redact failure")

        monkeypatch.setattr(brain_hook, "_redact_pii", broken_redactor)

        original = (
            "I decided to commit even though the redactor is broken — "
            "this thought must NOT be lost."
        )
        brain_hook._fire_capture(
            text=original,
            source="hook-test",
            session_id="s-1",
            project="testproj",
        )
        assert len(_FakePopen.instances) == 1, (
            "Fail-open broken: redact failure suppressed the entire capture"
        )
        captured = _extract_payload_text(_FakePopen.instances[0])
        assert captured == original, (
            f"Fail-open broken: captured={captured!r} expected={original!r}"
        )
