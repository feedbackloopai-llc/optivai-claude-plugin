#!/usr/bin/env python3
"""fblai-3yd1j — connect_timeout injection tests for _connect().

Verifies:
  (a) When DATABASE_URL has no connect_timeout, _connect() appends
      connect_timeout=10 to the DSN before calling psycopg2.connect.
  (b) When DATABASE_URL already contains connect_timeout=N, _connect()
      does NOT overwrite it.
  (c) Appending works correctly whether the DSN already has a '?' query
      section (append '&') or not (append '?').
  (d) connect_timeout is present in the DSN passed to psycopg2.connect
      in all cases (c1 + c2).

All tests mock psycopg2.connect and _get_database_url — no real DB
connection is made.

Run: python3 -m pytest scripts/tests/test_connect_timeout.py -v
"""
import os
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import open_brain  # noqa: E402


def _capture_connect_dsn(dsn_returned_by_get_url: str) -> str:
    """Run _connect() with a mocked DSN and return the DSN passed to psycopg2.connect."""
    captured = {}

    def mock_psycopg2_connect(dsn):
        captured["dsn"] = dsn
        fake_conn = mock.MagicMock()
        fake_conn.autocommit = False
        return fake_conn

    with mock.patch("open_brain._get_database_url", return_value=dsn_returned_by_get_url), \
         mock.patch("psycopg2.connect", side_effect=mock_psycopg2_connect):
        open_brain._connect()

    assert "dsn" in captured, "_connect() did not call psycopg2.connect"
    return captured["dsn"]


# ─── (a) connect_timeout appended when absent ────────────────────────────────

def test_connect_timeout_appended_when_absent_plain_dsn():
    """DSN with no query params: connect_timeout=10 must be appended with '?'."""
    base = "postgresql://user:pass@host/dbname"
    result = _capture_connect_dsn(base)

    assert "connect_timeout=10" in result, (
        f"Expected connect_timeout=10 in DSN; got: {result!r}"
    )
    assert "?" in result, (
        f"Expected '?' separator before connect_timeout; got: {result!r}"
    )


def test_connect_timeout_appended_when_absent_dsn_with_existing_param():
    """DSN with existing query params: connect_timeout=10 must be appended with '&'."""
    base = "postgresql://user:pass@host/dbname?sslmode=require"
    result = _capture_connect_dsn(base)

    assert "connect_timeout=10" in result, (
        f"Expected connect_timeout=10 appended to DSN; got: {result!r}"
    )
    # Must use '&' not '?' since there's already a '?'
    assert "sslmode=require&connect_timeout=10" in result or \
           "connect_timeout=10" in result.split("?", 1)[-1], (
        f"Expected connect_timeout to be appended after existing params with '&'; "
        f"got: {result!r}"
    )


# ─── (b) Existing connect_timeout not overwritten ────────────────────────────

def test_connect_timeout_not_overwritten_when_already_set():
    """When DATABASE_URL already has connect_timeout=30, _connect() must keep it."""
    base = "postgresql://user:pass@host/dbname?connect_timeout=30"
    result = _capture_connect_dsn(base)

    # The original value must be preserved
    assert "connect_timeout=30" in result, (
        f"Expected original connect_timeout=30 to be preserved; got: {result!r}"
    )

    # Must NOT add a second connect_timeout
    assert result.count("connect_timeout") == 1, (
        f"Expected exactly one connect_timeout in DSN; got: {result!r}"
    )


def test_connect_timeout_not_overwritten_when_set_in_other_position():
    """connect_timeout somewhere in the DSN (not at end) must not be duplicated."""
    base = "postgresql://user:pass@host/dbname?connect_timeout=5&sslmode=require"
    result = _capture_connect_dsn(base)

    assert result.count("connect_timeout") == 1, (
        f"Expected exactly one connect_timeout in DSN; got: {result!r}"
    )


# ─── (c) & (d) Both injection modes produce a valid DSN with connect_timeout ─

def test_neon_pooler_url_no_query():
    """Neon pooler URL (no query string) gets connect_timeout appended."""
    neon_url = "postgresql://user:secret@ep-xxx.neon.tech/neondb"
    result = _capture_connect_dsn(neon_url)

    assert "connect_timeout" in result, (
        f"Neon pooler URL must get connect_timeout; got: {result!r}"
    )
    # Must still start with the original URL
    assert result.startswith(neon_url), (
        f"Expected result to start with original URL; got: {result!r}"
    )


def test_neon_pooler_url_with_sslmode():
    """Neon URL with sslmode=require appends connect_timeout after existing param."""
    neon_url = "postgresql://user:secret@ep-xxx.neon.tech/neondb?sslmode=require"
    result = _capture_connect_dsn(neon_url)

    assert "connect_timeout" in result, (
        f"Expected connect_timeout in result; got: {result!r}"
    )
    # Must have both params
    assert "sslmode=require" in result, (
        f"Expected sslmode=require to be preserved; got: {result!r}"
    )


# ─── Edge case: empty connect_timeout value in DSN ───────────────────────────

def test_connect_timeout_substring_not_mismatched():
    """'no_connect_timeout_here' in DSN should not count as having connect_timeout."""
    # A DSN that mentions 'timeout' but NOT 'connect_timeout'
    base = "postgresql://user:pass@host/dbname?statement_timeout=30000"
    result = _capture_connect_dsn(base)

    # statement_timeout != connect_timeout — we must still inject connect_timeout
    assert "connect_timeout" in result, (
        f"statement_timeout != connect_timeout; connect_timeout must still be injected; "
        f"got: {result!r}"
    )
