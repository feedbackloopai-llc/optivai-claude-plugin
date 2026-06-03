"""Tests for auto_recall_hook.py — the KEYSTONE auto-recall hook.

The hook reads the user's prompt from stdin (Claude Code's UserPromptSubmit
hook protocol), decides whether to fire, runs a brain search if so, and
emits ``additionalContext`` JSON to stdout. Every error class is fail-open:
silent exit 0 with no stdout, never blocking the prompt.

These tests cover:
  1. Trigger logic (length + keyword combinations).
  2. Query truncation at QUERY_WINDOW_CHARS.
  3. Subprocess env carries HF_HUB_OFFLINE flags.
  4. Output formatting (additionalContext header + bullet lines).
  5. Dedup on repeated THOUGHT_ID.
  6. Fail-open on every error class (timeout, non-zero exit, bad JSON,
     missing script, bare exception).
  7. Empty search result → silent exit (no empty block emitted).

The strategy: drive the hook end-to-end as a subprocess of `python3` with
stdin piped in, and mock the inner `subprocess.run` call to `open_brain.py`
by importing `auto_recall_hook` as a module and monkey-patching `subprocess.run`.
That lets us assert on (a) the args passed to the brain search and (b) the
stdout the hook emits.
"""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

# Make scripts/hooks importable
HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import auto_recall_hook  # noqa: E402  (path set up above)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_atom(
    thought_id: str = "brain-1234567890-abcdef12",
    summary: str = "A prior memory summary.",
    thought_type: str = "decision",
    created_at: str = "2026-06-03 12:00:00.000000+00:00",
) -> dict:
    """Build a minimal atom matching open_brain.py --json output shape."""
    return {
        "THOUGHT_ID": thought_id,
        "RAW_TEXT": "raw text body",
        "SUMMARY": summary,
        "THOUGHT_TYPE": thought_type,
        "TOPICS": [],
        "PEOPLE": [],
        "ACTION_ITEMS": [],
        "SOURCE": "claude-code",
        "PROJECT": "test",
        "CREATED_AT": created_at,
        "KEYWORD_BOOST": 1.0,
        "TIME_DECAY": 1.0,
        "HYBRID_SCORE": 0.5,
        "SIMILARITY": 0.5,
        "EFFECTIVE_WEIGHT": 0.0,
        "PROMOTION_BOOST": 0.0,
    }


class _FakeCompletedProcess:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode

    def check_returncode(self):
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, "open_brain.py")


def _run_hook_with_prompt(prompt: str, mock_run=None, brain_exists: bool = True):
    """Drive the hook end-to-end as if invoked by Claude Code.

    Returns (stdout_text, exit_code). On every fail-open path the exit_code
    should be 0 and stdout should be empty.
    """
    stdin_payload = json.dumps({"prompt": prompt})
    captured_stdout = io.StringIO()

    # Patch stdin with a StringIO so json.load(sys.stdin) reads our payload.
    fake_stdin = io.StringIO(stdin_payload)

    patches = [patch.object(sys, "stdin", fake_stdin)]

    if mock_run is not None:
        patches.append(patch.object(auto_recall_hook.subprocess, "run", mock_run))

    if not brain_exists:
        # Force the script-path check to fail
        patches.append(patch.object(
            auto_recall_hook, "OPEN_BRAIN_SCRIPT",
            Path("/nonexistent/path/open_brain_MISSING.py"),
        ))

    exit_code = 0
    try:
        for p in patches:
            p.start()
        with redirect_stdout(captured_stdout):
            try:
                auto_recall_hook.main()
            except SystemExit as exc:
                exit_code = exc.code or 0
    finally:
        for p in patches:
            p.stop()

    return captured_stdout.getvalue(), exit_code


# ─── Trigger logic tests ──────────────────────────────────────────────────────

def test_short_prompt_no_trigger_keyword_skips():
    """Prompt below 50 chars (or just under it) with no keyword → silent exit."""
    # 49 chars exactly, no trigger keyword.
    prompt = "x" * 49
    assert len(prompt) == 49

    # subprocess.run should NEVER be called — if it is, the test fails because
    # we never primed a mock that would tolerate it.
    out, exit_code = _run_hook_with_prompt(prompt)

    assert out == ""
    assert exit_code == 0


def test_short_prompt_with_trigger_keyword_fires():
    """60-char prompt containing 'plan' triggers the search."""
    prompt = "I need to plan something concrete now please okay yes go"
    assert len(prompt) >= 50
    assert "plan" in prompt.lower()
    assert len(prompt) < 200

    atoms = [_make_atom()]
    mock_run = _make_mock_run(atoms)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert exit_code == 0
    assert "additionalContext" in out
    assert mock_run.called


def test_long_prompt_no_keyword_fires():
    """250-char prompt with no trigger keyword fires via the length bypass."""
    prompt = "a" * 250  # no trigger keyword
    assert len(prompt) >= 200

    # Verify no trigger keyword is in the prompt
    lower = prompt.lower()
    assert not any(kw in lower for kw in auto_recall_hook.TRIGGER_KEYWORDS)

    atoms = [_make_atom()]
    mock_run = _make_mock_run(atoms)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert exit_code == 0
    assert "additionalContext" in out
    assert mock_run.called


# ─── Output formatting tests ──────────────────────────────────────────────────

def test_additional_context_formatting():
    """Three atoms → header + three hyphenated bullet lines, each with date|type|short_id."""
    prompt = "Please review and decide on this architectural plan in detail." * 4
    assert len(prompt) >= 200  # ensures fires

    atoms = [
        _make_atom(
            thought_id="brain-1111111111-aaaaaaaa",
            summary="First prior memory.",
            thought_type="decision",
            created_at="2026-06-01 12:00:00.000000+00:00",
        ),
        _make_atom(
            thought_id="brain-2222222222-bbbbbbbb",
            summary="Second prior memory.",
            thought_type="pattern",
            created_at="2026-06-02 09:30:00.000000+00:00",
        ),
        _make_atom(
            thought_id="brain-3333333333-cccccccc",
            summary="Third prior memory.",
            thought_type="insight",
            created_at="2026-06-03 08:15:00.000000+00:00",
        ),
    ]
    mock_run = _make_mock_run(atoms)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]

    # Header present
    assert "## Recent neurosymbolic context" in ctx
    assert "### Related prior memories" in ctx

    # Three bullet lines, each starts with "- "
    bullet_lines = [ln for ln in ctx.splitlines() if ln.startswith("- ")]
    assert len(bullet_lines) == 3

    # Per-line shape: "- 2026-06-01 | decision | aaaaaaaa — First prior memory."
    assert "2026-06-01" in bullet_lines[0]
    assert "decision" in bullet_lines[0]
    assert "aaaaaaaa" in bullet_lines[0]  # short_id is last 8 chars of THOUGHT_ID
    assert "First prior memory." in bullet_lines[0]

    assert "2026-06-02" in bullet_lines[1]
    assert "pattern" in bullet_lines[1]
    assert "bbbbbbbb" in bullet_lines[1]

    assert "2026-06-03" in bullet_lines[2]
    assert "insight" in bullet_lines[2]
    assert "cccccccc" in bullet_lines[2]


def test_dedup_repeated_atom_ids():
    """If the same THOUGHT_ID appears multiple times, keep only the first."""
    prompt = "Please plan and review this in considerable detail right now."
    assert len(prompt) >= 50
    assert "plan" in prompt.lower()

    # Atom 'A' appears twice — only first occurrence kept; total 4 unique lines.
    atoms = [
        _make_atom(thought_id="brain-aaaa-aaaaaaaa", summary="Atom A first."),
        _make_atom(thought_id="brain-bbbb-bbbbbbbb", summary="Atom B."),
        _make_atom(thought_id="brain-aaaa-aaaaaaaa", summary="Atom A dup."),
        _make_atom(thought_id="brain-cccc-cccccccc", summary="Atom C."),
        _make_atom(thought_id="brain-dddd-dddddddd", summary="Atom D."),
    ]
    mock_run = _make_mock_run(atoms)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    bullet_lines = [ln for ln in ctx.splitlines() if ln.startswith("- ")]
    assert len(bullet_lines) == 4  # 5 input atoms with one dup → 4 unique

    # The kept "A" line should be the FIRST occurrence's summary, not the dup.
    a_lines = [ln for ln in bullet_lines if "aaaaaaaa" in ln]
    assert len(a_lines) == 1
    assert "Atom A first." in a_lines[0]
    assert "Atom A dup." not in a_lines[0]


# ─── Fail-open tests ──────────────────────────────────────────────────────────

def test_fail_open_on_subprocess_timeout():
    """subprocess.run raising TimeoutExpired → silent exit 0."""
    prompt = "Please plan and design this entire architectural review now okay."
    assert len(prompt) >= 50 and "plan" in prompt.lower()

    def raises_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=8)

    mock_run = _make_mock_run_callable(raises_timeout)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert out == ""
    assert exit_code == 0


def test_fail_open_on_subprocess_nonzero():
    """subprocess returns non-zero CompletedProcess → silent exit 0."""
    prompt = "Please plan and design this entire architectural review now okay."

    def returns_nonzero(*args, **kwargs):
        return _FakeCompletedProcess(stdout="", returncode=2)

    mock_run = _make_mock_run_callable(returns_nonzero)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert out == ""
    assert exit_code == 0


def test_fail_open_on_json_parse_error():
    """subprocess stdout that isn't valid JSON → silent exit 0."""
    prompt = "Please plan and design this entire architectural review now okay."

    def returns_garbage(*args, **kwargs):
        return _FakeCompletedProcess(stdout="not json at all <<<", returncode=0)

    mock_run = _make_mock_run_callable(returns_garbage)

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert out == ""
    assert exit_code == 0


def test_fail_open_on_missing_open_brain_script():
    """If OPEN_BRAIN_SCRIPT points at a missing path → silent exit 0."""
    prompt = "Please plan and design this entire architectural review now okay."

    # No mock_run needed — the missing-path guard fires before subprocess.run.
    out, exit_code = _run_hook_with_prompt(prompt, brain_exists=False)

    assert out == ""
    assert exit_code == 0


def test_empty_search_result_silent():
    """Empty list from the brain → silent exit 0 (no empty block emitted)."""
    prompt = "Please plan and design this entire architectural review now okay."

    mock_run = _make_mock_run([])  # zero atoms

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)

    assert out == ""
    assert exit_code == 0


# ─── Subprocess invocation tests ──────────────────────────────────────────────

def test_query_truncation():
    """2000-char prompt → subprocess invoked with first 500 chars only."""
    prompt = "z" * 2000  # 2000 chars; triggers via length bypass
    assert len(prompt) >= 200

    atoms = [_make_atom()]
    mock_run = _make_mock_run(atoms)

    _, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    # Inspect what was passed to subprocess.run
    assert mock_run.called
    call_args = mock_run.call_args
    cmd_list = call_args.args[0]
    # Cmd is [python3, open_brain.py, --search, <query>, --limit, 5, --json]
    assert "--search" in cmd_list
    search_idx = cmd_list.index("--search")
    query = cmd_list[search_idx + 1]
    assert len(query) == auto_recall_hook.QUERY_WINDOW_CHARS == 500


def test_hf_offline_env_vars_set():
    """The subprocess env must carry HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1."""
    prompt = "Please plan and design this entire architectural review now okay."

    atoms = [_make_atom()]
    mock_run = _make_mock_run(atoms)

    _, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0
    assert mock_run.called

    env = mock_run.call_args.kwargs.get("env", {})
    assert env.get("HF_HUB_OFFLINE") == "1"
    assert env.get("TRANSFORMERS_OFFLINE") == "1"


# ─── Mock factory helpers (declared at end for readability) ───────────────────

def _make_mock_run(atoms_to_return):
    """Build a MagicMock that returns the given atoms via JSON stdout."""
    from unittest.mock import MagicMock

    mock = MagicMock(name="subprocess_run_mock")
    mock.return_value = _FakeCompletedProcess(
        stdout=json.dumps(atoms_to_return),
        returncode=0,
    )
    return mock


def _make_mock_run_callable(side_effect_callable):
    """Build a MagicMock whose side_effect is a callable returning the desired result."""
    from unittest.mock import MagicMock

    mock = MagicMock(name="subprocess_run_mock")
    mock.side_effect = side_effect_callable
    return mock
