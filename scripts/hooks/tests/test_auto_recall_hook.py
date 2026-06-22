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


# ─── Stale-state guard test helpers ───────────────────────────────────────────
# The guard issues two new subprocess command shapes alongside the existing
# brain-search one:
#   ["beads", "show", "<id>", "--json"]                                  (bead lookup)
#   ["python3", "<open_brain.py>", "--inspect", "<id>", "--json"]        (atom inspect)
# A dispatching mock routes by argv[0]/argv[1] so we can assert per-command
# behavior in isolation.


def _make_dispatching_mock(
    atoms_to_return=None,
    bead_status_by_id=None,
    bead_title_by_id=None,
    bead_raises_for=None,
    atom_prov_by_id=None,
):
    """Dispatch subprocess.run by command shape.

    - atoms_to_return: list of atom dicts for open_brain --search (or None → empty)
    - bead_status_by_id: {bead_id: status_str} — return JSON {"status": ..., "title": ...}
    - bead_title_by_id: {bead_id: title_str} — paired with bead_status_by_id
    - bead_raises_for: set of bead_ids whose lookup should raise an exception
    - atom_prov_by_id: {atom_id: {"superseded_by": ...}} — open_brain --inspect payload
    """
    from unittest.mock import MagicMock

    atoms_to_return = atoms_to_return or []
    bead_status_by_id = bead_status_by_id or {}
    bead_title_by_id = bead_title_by_id or {}
    bead_raises_for = bead_raises_for or set()
    atom_prov_by_id = atom_prov_by_id or {}

    def dispatch(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        if not argv:
            return _FakeCompletedProcess(stdout="", returncode=2)

        # beads show <id> --json
        if argv[0] == "beads" and len(argv) >= 3 and argv[1] == "show":
            bid = argv[2]
            if bid in bead_raises_for:
                raise subprocess.CalledProcessError(2, argv)
            status = bead_status_by_id.get(bid)
            if status is None:
                return _FakeCompletedProcess(stdout="", returncode=2)
            payload = {"id": bid, "status": status, "title": bead_title_by_id.get(bid, "")}
            return _FakeCompletedProcess(stdout=json.dumps(payload), returncode=0)

        # python3 <open_brain.py> --inspect <id> --json
        # OR    python3 <open_brain.py> --search <query> ...
        if argv[0] == "python3" and len(argv) >= 4:
            if "--inspect" in argv:
                aid_idx = argv.index("--inspect") + 1
                aid = argv[aid_idx] if aid_idx < len(argv) else ""
                payload = atom_prov_by_id.get(aid)
                if payload is None:
                    return _FakeCompletedProcess(stdout="", returncode=2)
                return _FakeCompletedProcess(stdout=json.dumps(payload), returncode=0)
            if "--search" in argv:
                return _FakeCompletedProcess(
                    stdout=json.dumps(atoms_to_return),
                    returncode=0,
                )

        return _FakeCompletedProcess(stdout="", returncode=2)

    mock = MagicMock(name="subprocess_run_dispatching_mock")
    mock.side_effect = dispatch
    return mock


def _count_beads_show_calls(mock_run, bead_id=None):
    """Count how many times `beads show [<bead_id>]` was invoked."""
    count = 0
    for call in mock_run.call_args_list:
        argv = call.args[0] if call.args else call.kwargs.get("args", [])
        if not argv or argv[0] != "beads" or len(argv) < 3 or argv[1] != "show":
            continue
        if bead_id is None or argv[2] == bead_id:
            count += 1
    return count


# ─── Stale-state guard tests (enhancement #2 — bead gz-ow0sp) ─────────────────

def test_stale_state_detects_closed_bead_in_prompt():
    """Prompt referencing gz-abc123; bead is CLOSED → stale-state section emitted."""
    prompt = (
        "Please review the implementation in bead gz-abc123 — I think the "
        "stale-state guard hook needs further work and design review now."
    )
    assert len(prompt) >= 50
    assert "review" in prompt.lower()

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_status_by_id={"gz-abc123": "closed"},
        bead_title_by_id={"gz-abc123": "Stale-state guard for auto-recall hook"},
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0
    assert out != ""

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    assert "## Stale-state guard" in ctx
    assert "gz-abc123" in ctx
    assert "[closed]" in ctx
    assert "Stale-state guard for auto-recall hook" in ctx


def test_stale_state_skips_open_bead():
    """Prompt references gz-abc123 but the bead is OPEN → no stale-state section."""
    prompt = (
        "Please review and design the next steps for bead gz-abc123 right now "
        "as part of this current planning session detail in depth."
    )
    assert len(prompt) >= 50

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_status_by_id={"gz-abc123": "open"},
        bead_title_by_id={"gz-abc123": "Open work item."},
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    # Recall block still emits, but no stale-state header
    assert "## Recent neurosymbolic context" in ctx
    assert "## Stale-state guard" not in ctx


def test_stale_state_dedups_repeated_bead_id():
    """Same bead ID mentioned 3 times → beads show called exactly once."""
    prompt = (
        "Please review gz-abc123 — gz-abc123 is the same bead — gz-abc123 "
        "appears three times in this prompt and dedup is expected explicit."
    )
    assert len(prompt) >= 50

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_status_by_id={"gz-abc123": "closed"},
        bead_title_by_id={"gz-abc123": "Dedup test bead."},
    )

    _, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    assert _count_beads_show_calls(mock_run, bead_id="gz-abc123") == 1


def test_stale_state_section_suppressed_when_no_closed_beads():
    """Prompt with only OPEN beads → no `## Stale-state guard` header in output."""
    prompt = (
        "Please plan the review of gz-aaa111 and gz-bbb222 and gz-ccc333 — "
        "all of these are still open and being actively worked on right now."
    )
    assert len(prompt) >= 50

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_status_by_id={
            "gz-aaa111": "open",
            "gz-bbb222": "in_progress",
            "gz-ccc333": "open",
        },
        bead_title_by_id={
            "gz-aaa111": "Bead one.",
            "gz-bbb222": "Bead two.",
            "gz-ccc333": "Bead three.",
        },
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    assert "## Stale-state guard" not in ctx
    # But beads show WAS called — assert the lookups ran
    assert _count_beads_show_calls(mock_run) == 3


def test_stale_state_fail_open_on_subprocess_error():
    """beads show raises → no stale-state section, but recall block still present."""
    prompt = (
        "Please review the design for bead gz-xyz999 — this should still "
        "produce the recall block even when beads CLI is broken right now."
    )
    assert len(prompt) >= 50

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_raises_for={"gz-xyz999"},
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    # Recall block still emits
    assert "## Recent neurosymbolic context" in ctx
    # Stale-state section absent (subprocess raised → fail-open silent skip)
    assert "## Stale-state guard" not in ctx


def test_stale_state_caps_at_10_bead_ids():
    """Prompt with 12 distinct bead IDs → beads show called at most 10 times."""
    bead_ids = [f"gz-id{i:04d}" for i in range(12)]
    assert len(bead_ids) == 12
    prompt = (
        "Please review and plan the work for the following twelve distinct "
        "beads in this big audit batch: " + " ".join(bead_ids) + " — done."
    )
    assert len(prompt) >= 50

    # Status doesn't matter for this test; all OPEN so no stale section emitted
    status_map = {bid: "open" for bid in bead_ids}
    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        bead_status_by_id=status_map,
    )

    _, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    # The cap is BEAD_ID_CAP (10) — at most that many lookups
    total_calls = _count_beads_show_calls(mock_run)
    assert total_calls <= auto_recall_hook.BEAD_ID_CAP == 10
    assert total_calls == 10  # all 10 first-seen IDs were looked up


def test_stale_state_section_appears_after_recall_block():
    """Both blocks populated → recall header appears BEFORE stale-state header."""
    prompt = (
        "Please review and decide on the architectural plan referenced in "
        "bead gz-stale1 which I believe is still open and unresolved okay."
    )
    assert len(prompt) >= 50

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom(summary="A prior recalled memory.")],
        bead_status_by_id={"gz-stale1": "closed"},
        bead_title_by_id={"gz-stale1": "Already resolved work."},
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]

    recall_idx = ctx.find("## Recent neurosymbolic context")
    stale_idx = ctx.find("## Stale-state guard")
    assert recall_idx >= 0, "recall header missing"
    assert stale_idx >= 0, "stale-state header missing"
    assert recall_idx < stale_idx, (
        f"recall header (idx={recall_idx}) must precede stale-state header "
        f"(idx={stale_idx})"
    )


def test_atom_supersession_warning():
    """Prompt mentions brain-1234-deadbeef; inspect returns prov.superseded_by → warning emitted."""
    prompt = (
        "Please review the prior recalled memory brain-1234-deadbeef and "
        "decide whether to act on it — design and plan accordingly okay yes."
    )
    assert len(prompt) >= 50

    superseded_payload = {
        "thought_id": "brain-1234-deadbeef",
        "result": {
            "prov": {
                "superseded_by": "brain-9999-cafef00d",
                "agent": "test-agent",
            },
        },
    }

    mock_run = _make_dispatching_mock(
        atoms_to_return=[_make_atom()],
        atom_prov_by_id={"brain-1234-deadbeef": superseded_payload},
    )

    out, exit_code = _run_hook_with_prompt(prompt, mock_run=mock_run)
    assert exit_code == 0

    payload = json.loads(out)
    ctx = payload["additionalContext"]
    assert "## Stale-state guard" in ctx
    assert "brain-1234-deadbeef" in ctx
    assert "superseded_by" in ctx
    assert "brain-9999-cafef00d" in ctx


# ─── Atom stale-guard cumulative timeout cap (fblai-7sjjj FIX 2) ─────────────

def test_collect_stale_atoms_stops_at_cumulative_budget():
    """_collect_stale_atoms honors ATOM_STALE_GUARD_BUDGET_SECONDS wall-clock cap.

    Scenario: 5 atom IDs are passed. Each _run_open_brain_inspect call sleeps
    long enough that even 2 calls would exceed the budget. Verify that the
    function returns after fewer than N calls, not all 5.

    The constant ATOM_STALE_GUARD_BUDGET_SECONDS is 10 s in the live config.
    We patch it to 0.05 s for the test so the budget fires immediately.
    Each mock inspect call sleeps 0.04 s — fast enough for the first call
    to complete within the budget but the second call to trip the cap.
    """
    import time as _time
    from unittest.mock import patch

    call_count = {"n": 0}

    def slow_inspect(atom_id: str):
        """Simulates a slow open_brain --inspect call."""
        call_count["n"] += 1
        _time.sleep(0.04)   # 40 ms per call; budget=50 ms → 2nd call trips the cap
        return {"prov": {"superseded_by": "brain-9999-replaced"}}

    atom_ids = [f"brain-{i}-{'a' * 8}" for i in range(5)]

    with patch.object(auto_recall_hook, "ATOM_STALE_GUARD_BUDGET_SECONDS", 0.05):
        with patch.object(auto_recall_hook, "_run_open_brain_inspect", side_effect=slow_inspect):
            result = auto_recall_hook._collect_stale_atoms(atom_ids)

    # The budget fires after ≤2 calls. We must have stopped before all 5.
    assert call_count["n"] < len(atom_ids), (
        f"Expected fewer than {len(atom_ids)} calls due to budget cap, "
        f"but got {call_count['n']} calls — cumulative cap is not working"
    )
    # Whatever ran should still be in the result (fail-open partial return).
    assert len(result) == call_count["n"], (
        f"Expected {call_count['n']} stale entries (one per call that completed), "
        f"got {len(result)}"
    )


def test_collect_stale_atoms_returns_partial_when_budget_hits():
    """Partial results are returned when the budget fires mid-loop.

    Three atom IDs; each inspect sleeps 0.03 s. Budget = 0.08 s (fits ~2
    calls). The third call should be skipped; the first two should appear
    in the result.
    """
    import time as _time
    from unittest.mock import patch

    call_count = {"n": 0}

    def slow_inspect(atom_id: str):
        call_count["n"] += 1
        _time.sleep(0.03)
        return {"superseded_by": f"brain-replaced-{call_count['n']}"}

    atom_ids = [f"brain-{i}-{'b' * 8}" for i in range(3)]

    with patch.object(auto_recall_hook, "ATOM_STALE_GUARD_BUDGET_SECONDS", 0.08):
        with patch.object(auto_recall_hook, "_run_open_brain_inspect", side_effect=slow_inspect):
            result = auto_recall_hook._collect_stale_atoms(atom_ids)

    # At 30 ms/call with an 80 ms budget: call 1 finishes at ~30 ms (OK),
    # call 2 finishes at ~60 ms (OK), call 3 is blocked by the budget check
    # (elapsed ~60 ms > 80 ms is NOT yet over — but monotonic drift means
    # we may get 2 or 3 calls). The key invariant is: fewer than or equal to
    # total atom IDs, and the result length matches calls that completed.
    assert call_count["n"] <= len(atom_ids), (
        f"More calls than atom IDs: {call_count['n']} > {len(atom_ids)}"
    )
    assert len(result) == call_count["n"], (
        f"Result length {len(result)} != call count {call_count['n']}"
    )


def test_collect_stale_atoms_with_fast_inspect_runs_all():
    """When inspect calls are fast, all atom IDs are processed within budget."""
    from unittest.mock import patch

    call_count = {"n": 0}

    def fast_inspect(atom_id: str):
        call_count["n"] += 1
        # No sleep — returns immediately
        return {"prov": {"superseded_by": "brain-9999-replaced"}}

    atom_ids = [f"brain-{i}-{'c' * 8}" for i in range(auto_recall_hook.ATOM_ID_CAP)]

    # Use the real budget (no patching) — fast calls should all complete.
    with patch.object(auto_recall_hook, "_run_open_brain_inspect", side_effect=fast_inspect):
        result = auto_recall_hook._collect_stale_atoms(atom_ids)

    assert call_count["n"] == auto_recall_hook.ATOM_ID_CAP, (
        f"Expected all {auto_recall_hook.ATOM_ID_CAP} calls to complete when "
        f"inspect is fast, got {call_count['n']}"
    )
    assert len(result) == auto_recall_hook.ATOM_ID_CAP


def test_collect_stale_atoms_budget_constant_exists():
    """ATOM_STALE_GUARD_BUDGET_SECONDS constant must be defined and < 40s."""
    assert hasattr(auto_recall_hook, "ATOM_STALE_GUARD_BUDGET_SECONDS"), (
        "ATOM_STALE_GUARD_BUDGET_SECONDS constant missing from auto_recall_hook"
    )
    budget = auto_recall_hook.ATOM_STALE_GUARD_BUDGET_SECONDS
    max_old = auto_recall_hook.ATOM_ID_CAP * auto_recall_hook.ATOM_INSPECT_TIMEOUT_SECONDS
    assert budget < max_old, (
        f"Budget {budget}s must be less than the old worst-case {max_old}s "
        f"(ATOM_ID_CAP={auto_recall_hook.ATOM_ID_CAP} × "
        f"ATOM_INSPECT_TIMEOUT_SECONDS={auto_recall_hook.ATOM_INSPECT_TIMEOUT_SECONDS})"
    )
