#!/usr/bin/env python3
"""fblai-knutz — session_summary user_email removal test.

Verifies that get_provider_env() does NOT emit a 'user_email' field in the
returned dict under any circumstance, preventing PII from bypassing the
redaction layer in activity logs.

The fix chosen was outright removal (not hashing) because:
  - The field is not consumed by any downstream reader in the codebase.
  - Hashing preserves cardinality and leaks correlation identity even if not
    directly reversible — removal avoids that entirely.
  - Simpler: one line deleted, zero new logic.

Run: python3 -m pytest scripts/tests/test_session_summary_email.py -v
"""
import os
import sys
import unittest.mock as mock

import pytest

# Add hooks dir to path so we can import session_summary directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import session_summary  # noqa: E402


# ─── get_provider_env — teams branch ─────────────────────────────────────────


def test_teams_provider_has_no_user_email_field(monkeypatch):
    """Teams provider dict must NOT contain 'user_email' even when CLAUDE_USER_EMAIL is set."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv("CLAUDE_USER_EMAIL", "chris@example.com")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    result = session_summary.get_provider_env()

    assert "user_email" not in result, (
        f"'user_email' must not appear in provider dict; got keys: {list(result.keys())}"
    )


def test_teams_provider_has_no_user_email_when_env_absent(monkeypatch):
    """Teams provider dict must not contain 'user_email' even when CLAUDE_USER_EMAIL is absent."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.delenv("CLAUDE_USER_EMAIL", raising=False)

    result = session_summary.get_provider_env()

    assert "user_email" not in result, (
        f"'user_email' must never appear in provider dict; got keys: {list(result.keys())}"
    )


def test_teams_provider_retains_required_fields(monkeypatch):
    """Teams provider dict must still include type, model, and organization."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("CLAUDE_ORG_NAME", "TestOrg")

    result = session_summary.get_provider_env()

    assert result.get("type") == "teams"
    assert result.get("model") == "claude-opus-4-7"
    assert result.get("organization") == "TestOrg"


# ─── get_provider_env — bedrock branch ───────────────────────────────────────


def test_bedrock_provider_has_no_user_email(monkeypatch):
    """Bedrock provider dict must never contain 'user_email'."""
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_USER_EMAIL", "someone@example.com")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    result = session_summary.get_provider_env()

    assert "user_email" not in result, (
        f"Bedrock provider dict must not contain 'user_email'; got keys: {list(result.keys())}"
    )
    assert result.get("type") == "bedrock"


# ─── Raw email value must not appear in any nested key ───────────────────────


def test_raw_email_string_absent_from_provider_dict(monkeypatch):
    """The literal email address must not appear as a value anywhere in the provider dict."""
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    raw_email = "chrishughes@hotmail.com"
    monkeypatch.setenv("CLAUDE_USER_EMAIL", raw_email)

    result = session_summary.get_provider_env()

    # Recursively check all string values in the dict.
    def contains_email(obj, email):
        if isinstance(obj, str):
            return email in obj
        if isinstance(obj, dict):
            return any(contains_email(v, email) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return any(contains_email(v, email) for v in obj)
        return False

    assert not contains_email(result, raw_email), (
        f"Raw email address must not appear anywhere in provider dict; got: {result!r}"
    )
