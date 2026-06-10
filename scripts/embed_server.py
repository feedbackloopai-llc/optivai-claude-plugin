#!/usr/bin/env python3
"""ABOUTME: Warm embedding server — keeps the sentence-transformers model resident
ABOUTME: so open_brain.py queries embed in ~0.1s instead of paying a ~10s torch import.

Stdlib-only HTTP server bound to 127.0.0.1. Endpoints:
    GET  /health -> {"status": "ok", "model": ..., "uptime_s": ...}
    POST /embed  {"text": "..."} -> {"embedding": [...], "dim": 768}

Resource contract (gaming-rig friendly):
    - Device is CPU unless OPEN_BRAIN_EMBED_DEVICE says otherwise — never touches
      the GPU by default, even if a CUDA torch build is installed later.
    - torch threads capped via OPEN_BRAIN_EMBED_THREADS (default 8).
    - Idle watchdog exits the process after OPEN_BRAIN_EMBED_IDLE_S seconds
      (default 1800) without a request; 0 disables. open_brain.py auto-respawns
      the server on the next cache miss, so shutdown costs one slow query, ever.

The model is loaded BEFORE the port is bound: a client that connects mid-startup
gets connection-refused (and falls back to its local model) rather than a request
that hangs for the duration of the model load.

If the port is already bound, another instance is warm — exit 0, not an error.
"""
import errno
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMBED_MODEL = os.environ.get("OPEN_BRAIN_EMBED_MODEL", "all-mpnet-base-v2")
EMBED_DEVICE = os.environ.get("OPEN_BRAIN_EMBED_DEVICE", "cpu")
PORT = int(os.environ.get("OPEN_BRAIN_EMBED_PORT", "8474"))
IDLE_TIMEOUT_S = float(os.environ.get("OPEN_BRAIN_EMBED_IDLE_S", "1800"))
TORCH_THREADS = int(os.environ.get("OPEN_BRAIN_EMBED_THREADS", "8"))
WATCHDOG_POLL_S = 30.0
MAX_TEXT = 8000  # mirrors open_brain._generate_embedding truncation
MAX_BODY = 65536  # maximum accepted Content-Length in bytes; prevents unbounded reads


def make_handler(encode_fn, model_name, started_at, state):
    """Build a request handler around an injected encode function (testable without a model).

    ``state["last_request"]`` is stamped on every request so the idle watchdog
    can measure inactivity. The store is a single scalar write under the GIL,
    which is atomic in CPython — no lock needed for this access pattern.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: this runs headless under Task Scheduler
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["last_request"] = time.time()
            if self.path == "/health":
                self._json(200, {"status": "ok", "model": model_name,
                                 "uptime_s": round(time.time() - started_at, 1)})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            state["last_request"] = time.time()
            if self.path != "/embed":
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or length > MAX_BODY:
                    return self._json(413, {"error": "request too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "invalid JSON or Content-Length"})
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._json(400, {"error": "missing or empty 'text'"})
            try:
                vec = encode_fn(text[:MAX_TEXT])
            except Exception:
                return self._json(500, {"error": "embedding failed"})
            self._json(200, {"embedding": vec, "dim": len(vec)})

    return Handler


def start_idle_watchdog(server, state, idle_s, interval=WATCHDOG_POLL_S):
    """Shut ``server`` down after ``idle_s`` seconds without requests.

    Returns the watchdog thread, or None when idle_s <= 0 (disabled).
    shutdown() is called from a separate thread because calling it from a
    handler thread deadlocks ThreadingHTTPServer.
    """
    if idle_s <= 0:
        return None

    def watch():
        while True:
            time.sleep(interval)
            if time.time() - state["last_request"] > idle_s:
                threading.Thread(target=server.shutdown, daemon=True).start()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def main():
    started_at = time.time()
    from sentence_transformers import SentenceTransformer  # the slow import — paid once
    import torch
    torch.set_num_threads(TORCH_THREADS)
    model = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)

    def encode(text):
        return model.encode(text).tolist()

    state = {"last_request": time.time()}
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT),
                                     make_handler(encode, EMBED_MODEL, started_at, state))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit(0)  # port taken: another instance is already warm
        raise  # any other bind failure is a real error — let it surface
    start_idle_watchdog(server, state, IDLE_TIMEOUT_S)
    server.serve_forever()


if __name__ == "__main__":
    main()
