"""Tests for embed_server.py — handler logic with an injected fake encoder (no model, no network beyond loopback)."""
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from embed_server import make_handler, start_idle_watchdog
from http.server import ThreadingHTTPServer


def _make_server(encode_fn=lambda t: [0.1] * 768, state=None):
    state = state if state is not None else {"last_request": time.time()}
    handler = make_handler(encode_fn, "fake-model", 0.0, state)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}", state


@pytest.fixture
def server_url():
    srv, _thread, url, _state = _make_server()
    yield url
    srv.shutdown()


def _post(url, body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=5)


def test_health_returns_ok(server_url):
    with urllib.request.urlopen(server_url + "/health", timeout=5) as r:
        payload = json.load(r)
    assert r.status == 200
    assert payload["status"] == "ok"
    assert payload["model"] == "fake-model"


def test_embed_returns_768_dim_vector(server_url):
    with _post(server_url + "/embed", {"text": "hello brain"}) as r:
        payload = json.load(r)
    assert r.status == 200
    assert payload["dim"] == 768
    assert len(payload["embedding"]) == 768


def test_embed_truncates_to_8000_chars():
    seen = {}
    def encoder(t):
        seen["len"] = len(t)
        return [0.0]
    srv, _t, url, _s = _make_server(encode_fn=encoder)
    try:
        _post(url + "/embed", {"text": "x" * 9000})
        assert seen["len"] == 8000
    finally:
        srv.shutdown()


def test_embed_missing_text_is_400(server_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server_url + "/embed", {"nope": 1})
    assert exc.value.code == 400


def test_embed_invalid_json_is_400(server_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server_url + "/embed", b"not json{", raw=True)
    assert exc.value.code == 400


def test_unknown_path_is_404(server_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server_url + "/nope", timeout=5)
    assert exc.value.code == 404


def test_request_updates_last_request_state():
    state = {"last_request": 0.0}
    srv, _t, url, state = _make_server(state=state)
    try:
        _post(url + "/embed", {"text": "hi"})
        assert state["last_request"] > 0.0
    finally:
        srv.shutdown()


def test_idle_watchdog_shuts_down_after_idle():
    srv, thread, _url, state = _make_server(state={"last_request": time.time()})
    start_idle_watchdog(srv, state, idle_s=0.2, interval=0.05)
    thread.join(timeout=5)
    assert not thread.is_alive(), "server thread should exit after idle shutdown"


def test_idle_watchdog_disabled_when_zero():
    assert start_idle_watchdog(None, {"last_request": 0.0}, idle_s=0, interval=0.05) is None
