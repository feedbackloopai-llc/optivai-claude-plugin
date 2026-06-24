"""test_dispatch_env.py — VA0a: verify ANTHROPIC_API_KEY is stripped from dispatch env.

Bead: fblai-e4txq

Tests that _live_dispatch_with_cwd passes an explicit ``env=`` kwarg to
subprocess.run that does NOT contain ANTHROPIC_API_KEY, so that ``claude -p``
uses the Max-plan OAuth credentials rather than a potentially depleted API key
inherited from the parent process.

Run: python3 -m pytest scripts/tests/test_dispatch_env.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import _live_dispatch_with_cwd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeCompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture_subprocess_run_calls() -> tuple[List[Dict[str, Any]], Any]:
    """Return (captured_calls_list, mock_run) for patching subprocess.run."""
    captured: List[Dict[str, Any]] = []

    def _fake_run(args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(
            stdout='{"result": "ok", "usage": {"output_tokens": 5}}'
        )

    return captured, _fake_run


# ---------------------------------------------------------------------------
# Test 1 — ANTHROPIC_API_KEY is excluded from the subprocess env
# ---------------------------------------------------------------------------

class TestDispatchEnvStripsApiKey:

    def test_api_key_excluded_when_present_in_os_environ(self) -> None:
        """When ANTHROPIC_API_KEY is in os.environ, it must NOT appear in the
        env kwarg passed to subprocess.run."""
        captured, fake_run = _capture_subprocess_run_calls()

        fake_env = dict(os.environ)
        fake_env["ANTHROPIC_API_KEY"] = "sk-ant-test-key-should-be-stripped"

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("hello", "sonnet", 30)

        assert len(captured) == 1, f"Expected 1 subprocess.run call, got {len(captured)}"
        call_kwargs = captured[0]["kwargs"]

        assert "env" in call_kwargs, (
            "subprocess.run was not called with an explicit env= kwarg — "
            "ANTHROPIC_API_KEY cannot be stripped without it"
        )
        passed_env: Dict[str, str] = call_kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in passed_env, (
            f"ANTHROPIC_API_KEY was found in the subprocess env: "
            f"{passed_env.get('ANTHROPIC_API_KEY', '<missing>')!r}. "
            "It must be stripped so claude -p uses Max-plan OAuth."
        )

    def test_api_key_excluded_when_absent_from_os_environ(self) -> None:
        """When ANTHROPIC_API_KEY is absent from os.environ, it must not be
        injected into the subprocess env either."""
        captured, fake_run = _capture_subprocess_run_calls()

        fake_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("hello", "sonnet", 30)

        assert len(captured) == 1
        call_kwargs = captured[0]["kwargs"]
        assert "env" in call_kwargs
        passed_env = call_kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in passed_env

    def test_other_env_vars_are_inherited(self) -> None:
        """All env vars other than ANTHROPIC_API_KEY must be forwarded intact."""
        captured, fake_run = _capture_subprocess_run_calls()

        fake_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/test",
            "ANTHROPIC_API_KEY": "sk-ant-must-strip",
            "CLAUDE_MODEL": "claude-sonnet-4-6",
            "SOME_OTHER_VAR": "keep-me",
        }

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("test prompt", "sonnet", 60)

        assert len(captured) == 1
        passed_env = captured[0]["kwargs"]["env"]

        # Key we want stripped
        assert "ANTHROPIC_API_KEY" not in passed_env

        # Keys that must survive
        assert passed_env.get("PATH") == "/usr/bin:/bin", (
            "PATH was not forwarded to subprocess env"
        )
        assert passed_env.get("HOME") == "/home/test"
        assert passed_env.get("CLAUDE_MODEL") == "claude-sonnet-4-6"
        assert passed_env.get("SOME_OTHER_VAR") == "keep-me"

    def test_env_is_a_new_dict_not_os_environ_reference(self) -> None:
        """The env dict passed to subprocess.run must be a fresh copy, not a
        reference to os.environ itself (mutating it would affect the process)."""
        captured, fake_run = _capture_subprocess_run_calls()

        fake_env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "key"}

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("prompt", "sonnet", 30)

        passed_env = captured[0]["kwargs"]["env"]
        assert passed_env is not fake_env, (
            "subprocess.run received os.environ itself — any mutation would "
            "corrupt the parent process environment"
        )

    def test_multiple_api_key_vars_only_anthropic_stripped(self) -> None:
        """Only ANTHROPIC_API_KEY is stripped; OPENAI_API_KEY and others survive."""
        captured, fake_run = _capture_subprocess_run_calls()

        fake_env = {
            "ANTHROPIC_API_KEY": "strip-me",
            "OPENAI_API_KEY": "keep-me",
            "SOME_SERVICE_KEY": "also-keep",
        }

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("test", "sonnet", 30)

        passed_env = captured[0]["kwargs"]["env"]
        assert "ANTHROPIC_API_KEY" not in passed_env
        assert passed_env.get("OPENAI_API_KEY") == "keep-me"
        assert passed_env.get("SOME_SERVICE_KEY") == "also-keep"


# ---------------------------------------------------------------------------
# Test 2 — cwd kwarg is forwarded when provided
# ---------------------------------------------------------------------------

class TestDispatchEnvWithCwd:

    def test_cwd_forwarded_to_subprocess_run(self) -> None:
        """When cwd is provided, it must be passed as cwd= to subprocess.run."""
        captured, fake_run = _capture_subprocess_run_calls()
        expected_cwd = "/tmp/mayor-wt-fblai-e4txq-12345"

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("prompt", "sonnet", 30, cwd=expected_cwd)

        assert len(captured) == 1
        assert captured[0]["kwargs"].get("cwd") == expected_cwd, (
            f"Expected cwd={expected_cwd!r}, got {captured[0]['kwargs'].get('cwd')!r}"
        )

    def test_cwd_none_when_not_provided(self) -> None:
        """When cwd is omitted, subprocess.run receives cwd=None."""
        captured, fake_run = _capture_subprocess_run_calls()

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("prompt", "sonnet", 30)

        assert captured[0]["kwargs"].get("cwd") is None

    def test_cwd_and_env_both_set_in_same_call(self) -> None:
        """env= and cwd= must both be present in the same subprocess.run call."""
        captured, fake_run = _capture_subprocess_run_calls()
        test_cwd = "/tmp/worktree-test"

        fake_env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "strip"}

        with patch("loop_runner.os.environ", fake_env), \
             patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("prompt", "sonnet", 30, cwd=test_cwd)

        call_kwargs = captured[0]["kwargs"]
        assert "env" in call_kwargs and "cwd" in call_kwargs, (
            "Expected both env= and cwd= in subprocess.run kwargs"
        )
        assert call_kwargs["cwd"] == test_cwd
        assert "ANTHROPIC_API_KEY" not in call_kwargs["env"]


# ---------------------------------------------------------------------------
# Test 3 — Return value is parsed correctly
# ---------------------------------------------------------------------------

class TestDispatchEnvReturnValue:

    def test_tokens_and_output_parsed_from_json_response(self) -> None:
        """tokens and output are extracted from the JSON response."""
        fake_stdout = '{"result": "task done", "usage": {"output_tokens": 42}}'
        fake_run_result = _FakeCompletedProcess(stdout=fake_stdout)

        with patch("loop_runner.subprocess.run", return_value=fake_run_result):
            result = _live_dispatch_with_cwd("prompt", "sonnet", 30)

        assert result["tokens"] == 42
        assert result["output"] == "task done"

    def test_malformed_json_returns_zero_tokens(self) -> None:
        """Non-JSON stdout is handled gracefully (0 tokens, raw stdout as output)."""
        raw_output = "not json output"
        fake_run_result = _FakeCompletedProcess(stdout=raw_output)

        with patch("loop_runner.subprocess.run", return_value=fake_run_result):
            result = _live_dispatch_with_cwd("prompt", "sonnet", 30)

        assert result["tokens"] == 0
        assert result["output"] == raw_output


# ---------------------------------------------------------------------------
# Test 4 — --model is wired into the argv (fblai-y2en9)
# ---------------------------------------------------------------------------

class TestDispatchModelFlag:
    """Verify that _live_dispatch_with_cwd forwards the model tier as --model to
    the claude CLI argv.  Before fblai-y2en9 the flag was silently absent."""

    def _make_argv_capture(self):
        """Return (captured_calls, fake_run) that records subprocess.run args."""
        captured: List[Dict[str, Any]] = []

        def _fake_run(args, **kwargs):
            captured.append({"args": args, "kwargs": kwargs})
            return _FakeCompletedProcess(
                stdout='{"result": "ok", "usage": {"output_tokens": 1}}'
            )

        return captured, _fake_run

    def test_model_haiku_in_argv(self) -> None:
        """When model='haiku' is passed, '--model' 'haiku' must appear in the argv."""
        captured, fake_run = self._make_argv_capture()

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("say ok", "haiku", 30)

        assert len(captured) == 1
        args = captured[0]["args"]
        assert "--model" in args, f"'--model' flag missing from argv: {args}"
        model_idx = args.index("--model")
        assert model_idx + 1 < len(args), "'--model' has no following value in argv"
        assert args[model_idx + 1] == "haiku", (
            f"Expected '--model haiku', got '--model {args[model_idx + 1]}'"
        )

    def test_model_sonnet_in_argv(self) -> None:
        """When model='sonnet' is passed, '--model' 'sonnet' must appear in the argv."""
        captured, fake_run = self._make_argv_capture()

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("implement task", "sonnet", 30)

        args = captured[0]["args"]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "sonnet"

    def test_model_opus_in_argv(self) -> None:
        """When model='opus' is passed, '--model' 'opus' must appear in the argv."""
        captured, fake_run = self._make_argv_capture()

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("design the arch", "opus", 30)

        args = captured[0]["args"]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "opus"

    def test_model_follows_output_format_in_same_invocation(self) -> None:
        """Both '--output-format json' and '--model <tier>' appear in the same argv."""
        captured, fake_run = self._make_argv_capture()

        with patch("loop_runner.subprocess.run", side_effect=fake_run):
            _live_dispatch_with_cwd("do work", "haiku", 30)

        args = captured[0]["args"]
        assert "--output-format" in args and "json" in args, (
            f"--output-format json missing from argv: {args}"
        )
        assert "--model" in args, f"--model missing from argv: {args}"

    def test_fake_recorded_dispatch_sees_model(self) -> None:
        """A fake dispatch callable (as used in governor tests) receives the model
        arg so tier routing is visible end-to-end even in synthetic scenarios."""
        recorded_models: List[str] = []

        def _fake_dispatch(prompt: str, model: str, timeout_s: int) -> dict:
            recorded_models.append(model)
            return {"tokens": 1, "output": "ok"}

        # Simulate what _mayor_worker does: route_model → tier → dispatch(prompt, tier, ...)
        from loop_runner import route_model, LOOP_MODEL_MAP
        busywork_bead = {"id": "fblai-test", "title": "trivial cleanup", "labels": [], "body": ""}
        tier = route_model(busywork_bead)  # should return "haiku" for busywork title
        _fake_dispatch("prompt", tier, 30)

        assert recorded_models == ["haiku"], (
            f"Expected tier routing to produce 'haiku' for busywork bead, got {recorded_models}"
        )
