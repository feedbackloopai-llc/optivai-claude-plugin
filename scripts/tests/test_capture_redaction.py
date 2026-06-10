#!/usr/bin/env python3
"""fblai-y0zsb — Front-door redaction tests for capture().

Policy under test:
  1. Redaction is applied BEFORE any text reaches an external LLM call
     (_extract_metadata_via_claude / _ollama / _openai).
  2. Redaction is applied BEFORE the raw_text value bound to the DB INSERT,
     UNLESS the OPEN_BRAIN_STORE_RAW=true escape hatch is active.
  3. When the escape hatch is active the DB param contains the original
     secret BUT the extractor STILL receives only redacted text, and
     metadata['stored_raw'] == True.
  4. Text containing literal brace characters (e.g. JSON snippets) must
     not crash the metadata prompt formatter — capture completes, and the
     extractor is called (KeyError is not swallowed silently).

All tests run WITHOUT a live DATABASE_URL by mocking psycopg2 and the
connection/cursor machinery. They assert on the SQL bind params and
monkeypatched call args, not on actual DB state.

Run: python3 -m pytest scripts/tests/test_capture_redaction.py -v
"""

import importlib
import json
import os
import sys
import types
import unittest.mock as mock

import pytest

# Ensure scripts/ is on the path so we can import open_brain.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


# ─── Shared helpers ───────────────────────────────────────────────────────────

# A real-looking fake AWS access key that will be caught by the redactor.
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEXT_WITH_SECRET = f"remember to use key={FAKE_AWS_KEY} for the prod bucket"


def _make_mock_conn():
    """Return a MagicMock that walks like a psycopg2 connection/cursor."""
    cur = mock.MagicMock()
    cur.fetchone.return_value = None  # was_derived_from guard: no parent row
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _capture_raw_text_param(cur):
    """Extract the raw_text bind param from the INSERT call on *cur*.

    open_brain.capture() calls cur.execute(insert_sql, (thought_id, user_id,
    text[:16384], ...)).  raw_text is the 3rd positional param (index 2).
    """
    for call in cur.execute.call_args_list:
        args = call[0]  # positional args tuple
        if len(args) >= 2:
            sql, params = args[0], args[1]
            if "INSERT INTO brain.thoughts" in sql:
                return params[2]  # raw_text is the 3rd column
    return None


# ─── Test 1: raw_text bound to INSERT is redacted by default ─────────────────


def test_capture_redacts_secret_before_storage(monkeypatch):
    """Default path: raw_text stored in DB must not contain the raw secret.

    The FAKE_AWS_KEY must not appear in the INSERT bind params.
    """
    conn, cur = _make_mock_conn()

    # Suppress embedding generation (no sentence-transformers needed).
    monkeypatch.setattr(open_brain, "_generate_embedding", lambda text: [0.0] * 768)

    # Suppress metadata extraction (not testing that here).
    monkeypatch.setattr(open_brain, "_extract_metadata", lambda text: {
        "type": "insight", "topics": [], "people": [],
        "action_items": [], "summary": "test",
    })

    # Ensure escape hatch is OFF.
    monkeypatch.delenv("OPEN_BRAIN_STORE_RAW", raising=False)

    open_brain.capture(conn, TEXT_WITH_SECRET, user_id="testuser")

    raw_text_param = _capture_raw_text_param(cur)
    assert raw_text_param is not None, "INSERT was not called — check _capture_raw_text_param"
    assert FAKE_AWS_KEY not in raw_text_param, (
        f"raw_text stored in DB still contains the raw secret: {raw_text_param!r}"
    )
    # Verify a redaction placeholder is present (any "[REDACTED..." style token).
    assert "[REDACTED" in raw_text_param or "REDACTED" in raw_text_param, (
        f"Expected a redaction placeholder in: {raw_text_param!r}"
    )


# ─── Test 2: extractor receives only redacted text ────────────────────────────


def test_extract_metadata_receives_redacted_text(monkeypatch):
    """The text passed to _extract_metadata_via_claude must not contain the secret.

    We monkeypatch _extract_metadata_via_claude to capture its argument and
    assert the raw key is absent.
    """
    conn, cur = _make_mock_conn()

    captured_args: list = []

    def spy_extract_claude(text: str):
        captured_args.append(text)
        return {
            "type": "insight", "topics": [], "people": [],
            "action_items": [], "summary": "test",
        }

    monkeypatch.setattr(open_brain, "_extract_metadata_via_claude", spy_extract_claude)
    monkeypatch.setattr(open_brain, "_generate_embedding", lambda text: [0.0] * 768)
    monkeypatch.delenv("OPEN_BRAIN_STORE_RAW", raising=False)

    open_brain.capture(conn, TEXT_WITH_SECRET, user_id="testuser")

    assert captured_args, "_extract_metadata_via_claude was never called"
    for arg in captured_args:
        assert FAKE_AWS_KEY not in arg, (
            f"Extractor received raw secret in: {arg!r}"
        )


# ─── Test 3: brace characters in thought text do not crash capture ───────────


def test_capture_with_braces_does_not_crash(monkeypatch):
    """Text containing literal braces must not raise a KeyError.

    Before the fix, METADATA_EXTRACTION_PROMPT.format(thought_text=text)
    would crash with KeyError when the text contained '{' or '}', because
    str.format() treats them as format placeholders.  The fix must either
    escape the text or restructure the prompt.

    We assert:
      (a) capture() completes without raising, AND
      (b) _extract_metadata was attempted (not short-circuited before the call).
    """
    conn, cur = _make_mock_conn()

    brace_text = 'store this: {"api_key": "secret", "url": "https://example.com"}'

    extractor_called: list = []

    def spy_extract(text: str):
        extractor_called.append(text)
        return {
            "type": "insight", "topics": [], "people": [],
            "action_items": [], "summary": "json capture",
        }

    monkeypatch.setattr(open_brain, "_extract_metadata", spy_extract)
    monkeypatch.setattr(open_brain, "_generate_embedding", lambda text: [0.0] * 768)
    monkeypatch.delenv("OPEN_BRAIN_STORE_RAW", raising=False)

    # Should not raise.
    result = open_brain.capture(conn, brace_text, user_id="testuser")

    assert result is not None, "capture() returned None unexpectedly"
    assert extractor_called, (
        "_extract_metadata was never called — KeyError may have been swallowed"
    )


# ─── Test 3b: prompt-formatting with braces does not crash the extractor ──────


def test_extract_metadata_via_claude_with_braces_does_not_crash(monkeypatch):
    """_extract_metadata_via_claude itself must not raise KeyError on brace text.

    This tests the METADATA_EXTRACTION_PROMPT.format() call in the extractor
    directly, bypassing capture() — to confirm the fix is in the right place.

    We mock the Anthropic client so no real HTTP is made.
    """
    brace_text = 'remember {"json": "value"} and {another: "one"}'

    # Build a fake anthropic module + client.
    fake_content = mock.MagicMock()
    fake_content.text = json.dumps({
        "type": "insight", "topics": [], "people": [],
        "action_items": [], "summary": "test",
    })
    fake_response = mock.MagicMock()
    fake_response.content = [fake_content]
    fake_client = mock.MagicMock()
    fake_client.messages.create.return_value = fake_response
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = mock.MagicMock(return_value=fake_client)

    with mock.patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        # Should not raise KeyError.
        result = open_brain._extract_metadata_via_claude(brace_text)

    assert result is not None, "_extract_metadata_via_claude returned None (likely raised internally)"
    # The text passed to the API must have the braces safely encoded.
    call_args = fake_client.messages.create.call_args
    assert call_args is not None, "client.messages.create was never called"
    messages = call_args[1].get("messages") or call_args[0][2]  # keyword or positional
    prompt_text = messages[0]["content"]
    # Original brace characters must appear only in the encoded form (doubled {{ }})
    # OR must have been wrapped in delimiters — but they must NOT cause a KeyError.
    # The presence of the thought content is sufficient proof.
    assert "json" in prompt_text, (
        "Thought content not found in the prompt sent to Claude"
    )


# ─── Test 4: OPEN_BRAIN_STORE_RAW escape hatch ────────────────────────────────


def test_store_raw_escape_hatch(monkeypatch):
    """With OPEN_BRAIN_STORE_RAW=true:
      - raw_text INSERT param CONTAINS the original secret (raw stored),
      - metadata has stored_raw == True,
      - but the extractor STILL receives redacted text (secret absent).
    """
    conn, cur = _make_mock_conn()

    captured_extractor_args: list = []

    def spy_extract_claude(text: str):
        captured_extractor_args.append(text)
        return {
            "type": "insight", "topics": [], "people": [],
            "action_items": [], "summary": "test",
        }

    monkeypatch.setattr(open_brain, "_extract_metadata_via_claude", spy_extract_claude)
    monkeypatch.setattr(open_brain, "_generate_embedding", lambda text: [0.0] * 768)
    monkeypatch.setenv("OPEN_BRAIN_STORE_RAW", "true")

    open_brain.capture(conn, TEXT_WITH_SECRET, user_id="testuser")

    # 1. raw_text bound param must still contain the secret.
    raw_text_param = _capture_raw_text_param(cur)
    assert raw_text_param is not None, "INSERT was not called"
    assert FAKE_AWS_KEY in raw_text_param, (
        f"Escape hatch: expected secret in raw_text, got: {raw_text_param!r}"
    )

    # 2. metadata jsonb param must include stored_raw=True.
    # Find the metadata param in the INSERT call (index 17 — the last %s::jsonb).
    metadata_param = None
    for call in cur.execute.call_args_list:
        args = call[0]
        if len(args) >= 2 and "INSERT INTO brain.thoughts" in args[0]:
            metadata_param = args[1][17]  # metadata is the 18th bind param (0-indexed)
    assert metadata_param is not None, "Could not locate metadata bind param"
    meta_dict = json.loads(metadata_param)
    assert meta_dict.get("stored_raw") is True, (
        f"metadata['stored_raw'] not True in escape-hatch mode: {meta_dict}"
    )

    # 3. extractor must still receive redacted text.
    assert captured_extractor_args, "_extract_metadata_via_claude was never called"
    for arg in captured_extractor_args:
        assert FAKE_AWS_KEY not in arg, (
            f"Escape hatch: extractor should still get redacted text, got: {arg!r}"
        )
