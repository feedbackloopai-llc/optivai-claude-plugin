#!/usr/bin/env python3
"""fblai-knutz — embed_server body-cap and encode-error handling tests.

Verifies:
  (a) POST /embed with Content-Length > MAX_BODY returns 413 JSON and
      encode_fn is NOT called.
  (b) POST /embed where encode_fn raises returns 500 JSON with
      {"error": "embedding failed"}, not an unhandled exception.

Uses the make_handler(encode_fn, ...) injection pattern already present in
embed_server.py.  Instead of driving a real socket, we instantiate the Handler
class directly via object.__new__, wire mock rfile/wfile/headers/path/command,
and call do_POST() directly.  This mirrors the minimal-mock style used in
test_bridge_auth.py.

Run: python3 -m pytest scripts/tests/test_embed_server.py -v
"""
import io
import json
import os
import sys
import time
import unittest.mock as mock

import pytest

# Add scripts dir to path so we can import embed_server
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import embed_server  # noqa: E402


# ─── Minimal handler-invocation helpers ──────────────────────────────────────

def _make_handler_instance(encode_fn, path: str, content_length: int, body_bytes: bytes):
    """Return an instantiated Handler with mocked I/O ready for do_POST() calls.

    Does NOT go through BaseHTTPRequestHandler.__init__ or the socket lifecycle.
    The wfile is an in-memory BytesIO; call _read_response(instance) afterwards.
    """
    state = {"last_request": 0.0}
    HandlerClass = embed_server.make_handler(
        encode_fn=encode_fn,
        model_name="test-model",
        started_at=0.0,
        state=state,
    )

    handler = object.__new__(HandlerClass)

    # Wire the minimal attributes do_POST() depends on.
    handler.path = path
    handler.command = "POST"

    # headers dict-like: only Content-Length is read.
    handler.headers = {"Content-Length": str(content_length)}

    # rfile provides the body bytes.
    handler.rfile = io.BytesIO(body_bytes)

    # wfile accumulates the response.
    handler.wfile = io.BytesIO()

    # _json() calls send_response / send_header / end_headers / wfile.write.
    # We need these to write into handler.wfile cleanly.
    def _send_response(code):
        handler.wfile.write(f"HTTP/1.1 {code} \r\n".encode("ascii"))
        handler._status_code = code

    def _send_header(name, value):
        handler.wfile.write(f"{name}: {value}\r\n".encode("ascii"))

    def _end_headers():
        handler.wfile.write(b"\r\n")

    handler.send_response = _send_response
    handler.send_header = _send_header
    handler.end_headers = _end_headers
    handler._status_code = None

    return handler


def _call_do_post_and_parse(encode_fn, path: str, content_length: int, body_bytes: bytes):
    """Invoke do_POST() and return (status_code, response_body_dict).

    status_code is parsed from the raw response buffer (first HTTP line).
    response_body_dict is the JSON-decoded body portion.
    """
    handler = _make_handler_instance(encode_fn, path, content_length, body_bytes)
    handler.do_POST()

    raw = handler.wfile.getvalue().decode("utf-8", errors="replace")

    # Find the first status line: "HTTP/1.1 <code> ...\r\n"
    lines = raw.split("\r\n")
    status_code = int(lines[0].split(" ")[1]) if lines else 0

    # Body is the content after the double CRLF separator.
    body_str = raw.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in raw else ""
    body_dict = json.loads(body_str) if body_str.strip() else {}

    return status_code, body_dict


# ─── (a) Content-Length > MAX_BODY → 413, encode_fn NOT called ───────────────


def test_oversized_body_returns_413_and_encode_fn_not_called():
    """POST /embed with Content-Length > MAX_BODY must return 413 without calling encode_fn."""
    encode_calls = []

    def spy_encode(text):
        encode_calls.append(text)
        return [0.1] * 768

    body = json.dumps({"text": "hello"}).encode("utf-8")
    oversized_length = embed_server.MAX_BODY + 1

    # The body bytes are short (legitimate); only the Content-Length header is
    # oversized — the guard fires before rfile.read() so actual bytes are irrelevant.
    status, data = _call_do_post_and_parse(spy_encode, "/embed", oversized_length, body)

    assert status == 413, f"Expected 413 for oversized Content-Length, got {status}"
    assert "error" in data, f"Response body should contain 'error' key, got {data!r}"
    assert len(encode_calls) == 0, (
        f"encode_fn must NOT be called when Content-Length > MAX_BODY; "
        f"was called {len(encode_calls)} time(s)"
    )


def test_negative_content_length_returns_413_and_encode_fn_not_called():
    """POST /embed with a NEGATIVE Content-Length must return 413 without calling encode_fn.

    Regression guard for a real DoS: int("-1") = -1, and `-1 > MAX_BODY` is False,
    so the old guard skipped the size check and reached rfile.read(-1), which reads
    until socket close (unbounded). The hardened guard rejects length < 0.
    """
    encode_calls = []

    def spy_encode(text):
        encode_calls.append(text)
        return [0.1] * 768

    body = json.dumps({"text": "hello"}).encode("utf-8")

    # Content-Length: -1 — the malicious header.
    status, data = _call_do_post_and_parse(spy_encode, "/embed", -1, body)

    assert status == 413, f"Expected 413 for negative Content-Length, got {status}"
    assert "error" in data, f"Response body should contain 'error' key, got {data!r}"
    assert len(encode_calls) == 0, (
        f"encode_fn must NOT be called on negative Content-Length; "
        f"was called {len(encode_calls)} time(s)"
    )


def test_exact_max_body_is_not_rejected():
    """A request with Content-Length == MAX_BODY must NOT return 413."""
    body = json.dumps({"text": "hello"}).encode("utf-8")

    encode_called = []

    def stub_encode(text):
        encode_called.append(True)
        return [0.5] * 768

    status, _ = _call_do_post_and_parse(
        stub_encode, "/embed", embed_server.MAX_BODY, body
    )

    assert status != 413, (
        f"Content-Length == MAX_BODY should not be rejected with 413, got {status}"
    )


def test_normal_request_returns_200():
    """A normal /embed POST returns 200 with 'embedding' and 'dim' fields."""
    body = json.dumps({"text": "embed this"}).encode("utf-8")

    def good_encode(text):
        return [0.1] * 768

    status, data = _call_do_post_and_parse(good_encode, "/embed", len(body), body)

    assert status == 200, f"Expected 200 for valid request, got {status}"
    assert "embedding" in data, f"Response must contain 'embedding', got {data!r}"
    assert "dim" in data, f"Response must contain 'dim', got {data!r}"


# ─── (b) encode_fn raises → 500 JSON, not unhandled exception ────────────────


def test_encode_fn_exception_returns_500():
    """When encode_fn raises an exception, the handler must return 500 JSON."""

    def exploding_encode(text):
        raise RuntimeError("GPU out of memory")

    body = json.dumps({"text": "embed this please"}).encode("utf-8")
    status, data = _call_do_post_and_parse(exploding_encode, "/embed", len(body), body)

    assert status == 500, f"Expected 500 when encode_fn raises, got {status}"
    assert "error" in data, f"Response must contain 'error' key, got {data!r}"
    assert data["error"] == "embedding failed", (
        f"Error message should be 'embedding failed', got {data['error']!r}"
    )


def test_encode_fn_value_error_returns_500():
    """ValueError from encode_fn is caught and returns 500, not an unhandled traceback."""

    def bad_encode(text):
        raise ValueError("bad input shape")

    body = json.dumps({"text": "test"}).encode("utf-8")
    status, data = _call_do_post_and_parse(bad_encode, "/embed", len(body), body)

    assert status == 500, f"Expected 500 for ValueError in encode_fn, got {status}"
    assert data.get("error") == "embedding failed", (
        f"Expected 'embedding failed', got {data.get('error')!r}"
    )


def test_encode_fn_exception_not_propagated():
    """The exception from encode_fn must NOT propagate out of do_POST()."""

    def crash_encode(text):
        raise MemoryError("OOM")

    body = json.dumps({"text": "test"}).encode("utf-8")

    # If do_POST lets the exception propagate, this call will raise.
    # We assert it does NOT raise by completing without exception.
    try:
        _call_do_post_and_parse(crash_encode, "/embed", len(body), body)
    except Exception as exc:
        pytest.fail(
            f"do_POST() must not propagate encode_fn exceptions; got {type(exc).__name__}: {exc}"
        )
