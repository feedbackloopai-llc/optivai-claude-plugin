#!/usr/bin/env python3
"""fblai-knutz (review fix) — log_writer user_email removal test.

Verifies that AgentActivityLogger._get_provider_env() does NOT emit a
'user_email' field, mirroring the session_summary fix.  These log writers are
session-log emitters too; the PII-removal must cover them.

_get_provider_env() uses no instance state, so we call it as an unbound method
with a dummy self (None) to avoid the heavy AgentActivityLogger.__init__ which
creates directories and writes session metadata.

Run: python3 -m pytest scripts/tests/test_log_writer_email.py -v
"""
import os
import sys

import pytest

# Add scripts dir to path so we can import log_writer (the scripts/ copy).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import log_writer  # noqa: E402


def _provider_env():
    """Call _get_provider_env without constructing the full logger."""
    return log_writer.AgentActivityLogger._get_provider_env(None)


# ─── teams branch ─────────────────────────────────────────────────────────────


def test_teams_provider_has_no_user_email_when_env_set(monkeypatch):
    """Teams provider dict must NOT contain 'user_email' even when CLAUDE_USER_EMAIL is set."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv("CLAUDE_USER_EMAIL", "chris@example.com")

    result = _provider_env()

    assert result.get("type") == "teams"
    assert "user_email" not in result, (
        f"'user_email' must not appear in log_writer provider dict; got keys: {list(result.keys())}"
    )


def test_teams_provider_has_no_user_email_when_env_absent(monkeypatch):
    """Teams provider dict must not contain 'user_email' even when CLAUDE_USER_EMAIL is absent."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.delenv("CLAUDE_USER_EMAIL", raising=False)

    result = _provider_env()

    assert "user_email" not in result, (
        f"'user_email' must never appear; got keys: {list(result.keys())}"
    )


def test_teams_provider_retains_required_fields(monkeypatch):
    """Teams provider dict must still include type, model, and organization."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("CLAUDE_ORG_NAME", "TestOrg")

    result = _provider_env()

    assert result.get("type") == "teams"
    assert result.get("model") == "claude-opus-4-7"
    assert result.get("organization") == "TestOrg"


# ─── bedrock branch ───────────────────────────────────────────────────────────


def test_bedrock_provider_has_no_user_email(monkeypatch):
    """Bedrock provider dict must never contain 'user_email'."""
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_USER_EMAIL", "someone@example.com")

    result = _provider_env()

    assert result.get("type") == "bedrock"
    assert "user_email" not in result, (
        f"Bedrock provider dict must not contain 'user_email'; got keys: {list(result.keys())}"
    )


# ─── raw email value absent anywhere ─────────────────────────────────────────


def test_raw_email_absent_from_provider_dict(monkeypatch):
    """The literal email address must not appear as a value anywhere in the provider dict."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    raw_email = "chrishughes@hotmail.com"
    monkeypatch.setenv("CLAUDE_USER_EMAIL", raw_email)

    result = _provider_env()

    def contains_email(obj, email):
        if isinstance(obj, str):
            return email in obj
        if isinstance(obj, dict):
            return any(contains_email(v, email) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return any(contains_email(v, email) for v in obj)
        return False

    assert not contains_email(result, raw_email), (
        f"Raw email must not appear anywhere in provider dict; got: {result!r}"
    )
