#!/usr/bin/env python3
"""fblai-3yd1j — Warm embed_server integration tests for _generate_embedding().

Verifies:
  (a) When the server is healthy (mock HTTP), _generate_embedding() returns
      the server's embedding and does NOT load the local model.
  (b) When the server is down (connection refused), _generate_embedding()
      falls back to the local model AND attempts a detached spawn of
      embed_server.py (mock subprocess.Popen, assert called).
  (c) A malformed / wrong-dim server response falls back to the local model.
  (d) A server health-check timeout falls back to the local model.
  (e) A server with status != "ok" falls back to the local model.

All tests mock HTTP calls and subprocess — no real embed server is needed.

Run: python3 -m pytest scripts/tests/test_warm_embed_server.py -v
"""
import json
import os
import sys
import urllib.error
import unittest.mock as mock
from io import BytesIO
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import open_brain  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_mock_urlopen_healthy(embedding: list):
    """Return a urlopen side_effect that responds with a healthy server and the given embedding."""
    call_count = [0]

    def urlopen_side_effect(request, timeout=None):
        call_count[0] += 1
        url = request.full_url if hasattr(request, "full_url") else str(request)

        if "/health" in url:
            body = json.dumps({"status": "ok", "model": "all-mpnet-base-v2", "uptime_s": 42.0})
        elif "/embed" in url:
            body = json.dumps({"embedding": embedding, "dim": len(embedding)})
        else:
            raise urllib.error.URLError("unexpected path")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = body.encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    return urlopen_side_effect


def _make_connection_refused_urlopen():
    """Return a urlopen side_effect that raises ConnectionRefusedError (server down)."""
    def urlopen_side_effect(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    return urlopen_side_effect


# ─── (a) Server healthy → use server, no local model load ────────────────────

def test_warm_server_used_when_healthy():
    """When server is healthy, _generate_embedding returns server result, no local model load."""
    server_embedding = [0.42] * 768

    local_model_loaded = []

    def mock_get_model():
        local_model_loaded.append(True)
        m = mock.MagicMock()
        m.encode.return_value = mock.MagicMock(tolist=lambda: [0.0] * 768)
        return m

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_healthy(server_embedding)), \
         mock.patch("open_brain._get_embedding_model", side_effect=mock_get_model):

        result = open_brain._generate_embedding("test text")

    assert result == server_embedding, (
        f"Expected server embedding {server_embedding[:3]}...; got {result[:3]}..."
    )
    assert len(local_model_loaded) == 0, (
        "Local model must NOT be loaded when the warm server is healthy; "
        f"it was loaded {len(local_model_loaded)} time(s)"
    )


def test_warm_server_returns_correct_length():
    """Server response must produce a 768-element list."""
    server_embedding = [float(i % 10) / 10.0 for i in range(768)]

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_healthy(server_embedding)), \
         mock.patch("open_brain._get_embedding_model"):

        result = open_brain._generate_embedding("length test")

    assert len(result) == 768, (
        f"Expected 768-element embedding from server; got {len(result)}"
    )


# ─── (b) Server down → fallback + spawn ──────────────────────────────────────

def test_server_down_falls_back_to_local_model():
    """When server is down (connection refused), local model is used."""
    local_embedding = [0.99] * 768
    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_connection_refused_urlopen()), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached") as mock_spawn:

        result = open_brain._generate_embedding("fallback test")

    assert result == local_embedding, (
        f"Expected local model embedding on server-down; got {result[:3]}..."
    )


def test_server_down_attempts_detached_spawn():
    """When server is down, _spawn_embed_server_detached must be called."""
    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: [0.0] * 768)

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_connection_refused_urlopen()), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached") as mock_spawn:

        open_brain._generate_embedding("spawn test")

    mock_spawn.assert_called_once(), (
        f"Expected _spawn_embed_server_detached to be called once when server is down; "
        f"called {mock_spawn.call_count} time(s)"
    )


def test_spawn_embed_server_detached_calls_popen():
    """_spawn_embed_server_detached() must call subprocess.Popen with start_new_session=True."""
    import subprocess

    with mock.patch("subprocess.Popen") as mock_popen, \
         mock.patch.object(Path, "exists", return_value=True):

        open_brain._spawn_embed_server_detached()

    mock_popen.assert_called_once()
    call_kwargs = mock_popen.call_args[1]

    assert call_kwargs.get("start_new_session") is True, (
        f"Expected start_new_session=True in Popen call; got kwargs: {call_kwargs!r}"
    )


def test_spawn_embed_server_detached_no_raise_on_popen_error():
    """_spawn_embed_server_detached must not propagate Popen exceptions."""
    import subprocess

    with mock.patch("subprocess.Popen", side_effect=OSError("no python")), \
         mock.patch.object(Path, "exists", return_value=True):

        # Must not raise
        try:
            open_brain._spawn_embed_server_detached()
        except Exception as exc:
            pytest.fail(
                f"_spawn_embed_server_detached must swallow errors; got: {exc!r}"
            )


# ─── (c) Wrong-dim server response → fallback to local ───────────────────────

def test_wrong_dim_server_response_falls_back_to_local():
    """When server returns wrong-dim embedding (384 instead of 768), use local model."""
    wrong_dim_embedding = [0.1] * 384  # wrong dim
    local_embedding = [0.5] * 768

    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_healthy(wrong_dim_embedding)), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached"):

        result = open_brain._generate_embedding("wrong dim test")

    assert result == local_embedding, (
        f"Expected local fallback on wrong-dim server response; got {result[:3]}..."
    )
    assert len(result) == 768


def test_malformed_server_response_falls_back_to_local():
    """When server returns non-list embedding field, use local model."""
    local_embedding = [0.7] * 768
    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    def urlopen_malformed(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "/health" in url:
            body = json.dumps({"status": "ok", "model": "test", "uptime_s": 1.0})
        else:
            # embedding is a string, not a list — malformed
            body = json.dumps({"embedding": "not_a_list", "dim": 768})
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = body.encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    with mock.patch("urllib.request.urlopen", side_effect=urlopen_malformed), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached"):

        result = open_brain._generate_embedding("malformed test")

    assert result == local_embedding, (
        f"Expected local fallback on malformed server response; got {result[:3]}..."
    )


# ─── (d) Health-check timeout → fallback to local ────────────────────────────

def test_health_check_timeout_falls_back_to_local():
    """When the health check times out, local model is used."""
    import socket
    local_embedding = [0.3] * 768
    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    def urlopen_timeout(request, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    with mock.patch("urllib.request.urlopen", side_effect=urlopen_timeout), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached"):

        result = open_brain._generate_embedding("timeout test")

    assert result == local_embedding, (
        f"Expected local fallback on health check timeout; got {result[:3]}..."
    )


# ─── (e) Server health status != "ok" → fallback to local ────────────────────

def test_unhealthy_server_status_falls_back_to_local():
    """When server returns status != 'ok' on /health, local model is used."""
    local_embedding = [0.2] * 768
    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    def urlopen_unhealthy(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "/health" in url:
            body = json.dumps({"status": "loading", "model": "test"})
        else:
            body = json.dumps({"embedding": [0.1] * 768, "dim": 768})
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = body.encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    with mock.patch("urllib.request.urlopen", side_effect=urlopen_unhealthy), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached"):

        result = open_brain._generate_embedding("unhealthy test")

    assert result == local_embedding, (
        f"Expected local fallback when server status != 'ok'; got {result[:3]}..."
    )


# ─── (f) Server running a DIFFERENT model → fallback to local (review FIX 1) ──
#
# Break-input: a leftover/idle embed_server (idle timeout 1800s) running a
# DIFFERENT 768-dim model.  status=="ok", dim==768 — both checks pass — but the
# vector lives in an incompatible embedding space and would silently poison
# cosine recall when stored with embed_model='all-mpnet-base-v2'.  The
# model-identity guard must reject it.

def _make_mock_urlopen_with_model(server_model: str, embedding: list):
    """urlopen side_effect: healthy server reporting `server_model`, returns `embedding`."""
    def urlopen_side_effect(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "/health" in url:
            body = json.dumps({"status": "ok", "model": server_model, "uptime_s": 99.0})
        elif "/embed" in url:
            body = json.dumps({"embedding": embedding, "dim": len(embedding)})
        else:
            raise urllib.error.URLError("unexpected path")
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = body.encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp
    return urlopen_side_effect


def test_try_warm_embed_server_returns_none_on_model_mismatch():
    """_try_warm_embed_server must return None when server's model != EMBED_MODEL.

    Even with status=='ok' AND a 768-dim embedding (same dim, different model).
    This is the direct unit test of the model-identity guard.
    """
    # A different but same-dim model (paraphrase-multilingual-mpnet-base-v2 is 768d).
    different_model = "paraphrase-multilingual-mpnet-base-v2"
    assert different_model != open_brain.EMBED_MODEL, (
        "Test precondition: the mismatch model must differ from EMBED_MODEL"
    )
    incompatible_but_768d = [0.123] * 768

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_with_model(different_model, incompatible_but_768d)):
        result = open_brain._try_warm_embed_server("model mismatch unit test")

    assert result is None, (
        "_try_warm_embed_server must return None when the server reports a model "
        f"!= EMBED_MODEL ('{open_brain.EMBED_MODEL}'); the dim==768 check cannot "
        f"catch a same-dim-different-model server.  Got: {result!r}"
    )


def test_model_mismatch_falls_back_to_local_model():
    """_generate_embedding falls back to local load when server runs a different model.

    Full-path test: status==ok, dim==768, model != EMBED_MODEL → local model
    is used and a detached spawn is NOT relied upon for correctness (the local
    result is what gets returned).
    """
    different_model = "paraphrase-multilingual-mpnet-base-v2"
    incompatible_but_768d = [0.456] * 768
    local_embedding = [0.789] * 768

    mock_model = mock.MagicMock()
    mock_model.encode.return_value = mock.MagicMock(tolist=lambda: local_embedding)

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_with_model(different_model, incompatible_but_768d)), \
         mock.patch("open_brain._get_embedding_model", return_value=mock_model), \
         mock.patch("open_brain._spawn_embed_server_detached"):

        result = open_brain._generate_embedding("model mismatch full-path test")

    assert result == local_embedding, (
        "Expected local fallback when server runs a different model; "
        f"got the (incompatible) server vector instead: {result[:3]}..."
    )
    # Critically, the incompatible server vector must NOT be returned.
    assert result != incompatible_but_768d, (
        "The incompatible same-dim server vector leaked through the model guard"
    )


def test_matching_model_still_uses_server():
    """Sanity: when the server's model MATCHES EMBED_MODEL, the server is used (no regression)."""
    server_embedding = [0.321] * 768

    local_loaded = []

    def mock_get_model():
        local_loaded.append(True)
        m = mock.MagicMock()
        m.encode.return_value = mock.MagicMock(tolist=lambda: [0.0] * 768)
        return m

    with mock.patch("urllib.request.urlopen",
                    side_effect=_make_mock_urlopen_with_model(open_brain.EMBED_MODEL, server_embedding)), \
         mock.patch("open_brain._get_embedding_model", side_effect=mock_get_model):

        result = open_brain._generate_embedding("matching model test")

    assert result == server_embedding, (
        f"Server with matching model must be used; got {result[:3]}..."
    )
    assert len(local_loaded) == 0, (
        "Local model must NOT load when the server's model matches EMBED_MODEL"
    )
