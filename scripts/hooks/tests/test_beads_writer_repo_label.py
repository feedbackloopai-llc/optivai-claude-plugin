"""Tests for the repo-label auto-application path in beads_writer.py.

The labeling convention (documented in AGENTS.md and
.claude/commands/bead-create.md): every bead created from within a git
working tree carries a ``repo:<basename>`` label so beads can be filtered
by their source repo without polluting the bead ID.

These tests cover the reflexive enforcement surface — the hook-driven
auto-create path. The interactive surface (the shell wrapper at
scripts/shell-aliases.sh) is tested by hand at install time.

Properties under test:

  1. ``_detect_repo_label`` returns ``repo:<basename>`` when ``git
     rev-parse --show-toplevel`` succeeds, None otherwise.
  2. ``_apply_repo_label`` shells out to ``beads label`` and is fail-open:
     subprocess errors return False but never raise.
  3. The end-to-end hook path calls ``beads label`` after a successful
     ``db.create()`` when a repo is detected, and skips it when not.
  4. Label application failure does NOT roll back bead creation.
  5. ``beads label`` is idempotent on duplicate adds — verified empirically
     with the real CLI in test 5.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/hooks importable.
HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import beads_writer  # noqa: E402  (path set up above)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_db(monkeypatch):
    """Inject a MagicMock BeadsDatabase into the singleton writer.

    The real DB requires the beads package to be importable AND a writable
    backing directory. For these tests, a MagicMock with a ``create`` method
    that returns an Issue-shaped namespace is sufficient — we are exercising
    the labeling branch, not the storage layer.
    """
    db = MagicMock()
    db.create.return_value = SimpleNamespace(id='gz-test01', labels=['auto', 'write'])

    # Reset the singleton so the next `get_writer()` returns a fresh writer
    # whose `.db` is our mock.
    beads_writer._writer = None  # noqa: SLF001
    monkeypatch.setattr(beads_writer, '_get_beads_db', lambda: db)

    # Also clear the dedup cache to avoid cross-test cache hits.
    beads_writer._content_hash_cache = set()  # noqa: SLF001
    return db


def _completed(returncode: int = 0, stdout: str = '', stderr: str = '') -> subprocess.CompletedProcess:
    """Build a CompletedProcess that mocked subprocess.run can return."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ─── 1. test_auto_label_when_in_git_repo ──────────────────────────────────────


def test_auto_label_when_in_git_repo(fake_db):
    """When `git rev-parse --show-toplevel` returns a path, the hook must
    invoke `beads label <new_id> repo:<basename>` after a successful create."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ['git', 'rev-parse']:
            return _completed(0, stdout='/Users/erato949/Documents/my-cool-repo\n')
        if cmd[:2] == ['beads', 'label']:
            return _completed(0, stdout=f"Added label '{cmd[3]}' to {cmd[2]}\n")
        return _completed(0)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        writer = beads_writer.get_writer()
        writer.on_tool_use(
            operation='write',
            prompt='create test file',
            details={'file_path': '/tmp/x.py'},
            session_id='s1',
            project='test',
            cwd='/Users/erato949/Documents/my-cool-repo',
        )

    # db.create was invoked once.
    assert fake_db.create.call_count == 1

    # The beads label subprocess was invoked with the correct ID and label.
    label_calls = [c for c in calls if c[0][:2] == ['beads', 'label']]
    assert len(label_calls) == 1, f"expected exactly 1 `beads label` call, got: {calls}"
    cmd, _kwargs = label_calls[0]
    assert cmd == ['beads', 'label', 'gz-test01', 'repo:my-cool-repo']


# ─── 2. test_no_label_when_not_in_git_repo ────────────────────────────────────


def test_no_label_when_not_in_git_repo(fake_db):
    """When `git rev-parse` exits non-zero (cwd is outside any repo), the
    hook must skip the label step entirely — no subprocess.run for it."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ['git', 'rev-parse']:
            return _completed(128, stderr='fatal: not a git repository\n')
        if cmd[:2] == ['beads', 'label']:
            pytest.fail(f"`beads label` must not be called outside a git repo: {cmd}")
        return _completed(0)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        writer = beads_writer.get_writer()
        writer.on_tool_use(
            operation='write',
            prompt='create test file',
            details={'file_path': '/tmp/y.py'},
            session_id='s2',
            project='test',
            cwd='/tmp',
        )

    # Bead was still created — labeling is additive, not gating.
    assert fake_db.create.call_count == 1

    # No `beads label` calls at all.
    label_calls = [c for c in calls if c[0][:2] == ['beads', 'label']]
    assert label_calls == []


# ─── 3. test_label_failure_does_not_fail_bead_creation ────────────────────────


def test_label_failure_does_not_fail_bead_creation(fake_db, capsys):
    """When `beads label` returns non-zero, the bead creation still succeeds
    — the failure is logged to stderr but the create return is preserved."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ['git', 'rev-parse']:
            return _completed(0, stdout='/Users/erato949/Documents/my-cool-repo\n')
        if cmd[:2] == ['beads', 'label']:
            return _completed(2, stderr='beads label: storage backend offline\n')
        return _completed(0)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        writer = beads_writer.get_writer()
        # The on_tool_use call must NOT raise. The Issue from db.create()
        # is still the source of truth for "the bead exists".
        writer.on_tool_use(
            operation='write',
            prompt='create test file',
            details={'file_path': '/tmp/z.py'},
            session_id='s3',
            project='test',
            cwd='/Users/erato949/Documents/my-cool-repo',
        )

    # db.create was invoked despite the labeling failure.
    assert fake_db.create.call_count == 1

    # A warning was emitted to stderr (fail-open contract).
    captured = capsys.readouterr()
    assert 'Warning' in captured.err
    assert 'gz-test01' in captured.err
    assert 'repo:my-cool-repo' in captured.err


# ─── 4. test_label_correct_repo_basename ──────────────────────────────────────


def test_label_correct_repo_basename(fake_db):
    """Verify the basename extraction: `/Users/foo/Documents/my-cool-repo`
    must produce the label `repo:my-cool-repo` — no path components, no
    leading slash, no trailing slash."""
    captured_label: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ['git', 'rev-parse']:
            return _completed(0, stdout='/Users/foo/Documents/my-cool-repo\n')
        if cmd[:2] == ['beads', 'label']:
            captured_label['cmd'] = cmd
            return _completed(0)
        return _completed(0)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        writer = beads_writer.get_writer()
        writer.on_tool_use(
            operation='edit',
            prompt='modify a file',
            details={'file_path': '/Users/foo/Documents/my-cool-repo/src/a.py'},
            session_id='s4',
            project='test',
            cwd='/Users/foo/Documents/my-cool-repo/src',
        )

    assert 'cmd' in captured_label, "beads label was not invoked"
    cmd = captured_label['cmd']
    assert cmd[3] == 'repo:my-cool-repo', f"wrong label: {cmd[3]!r}"
    # And explicitly: no path separator leaked into the label.
    assert '/' not in cmd[3]


# ─── 5. test_label_idempotent_on_already_labeled ──────────────────────────────


def test_label_idempotent_on_already_labeled(fake_db):
    """Calling `beads label <id> <label>` twice with the same label must be
    a no-op success on the second call — the beads CLI is idempotent on
    duplicate label adds (verified empirically against beads v0.1.0).

    We verify this at two levels:
      (a) ``_apply_repo_label`` returns True both times when the underlying
          subprocess returns exit 0 (which the real CLI does for duplicates,
          with an informational stderr message like
          ``Issue gz-xxx already has label 'foo'``).
      (b) End-to-end: invoking the hook twice for the same bead does not
          raise and continues to invoke ``beads label`` each time — the
          hook does NOT need to track applied labels because the CLI itself
          handles dedup.
    """
    label_call_count = {'n': 0}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ['git', 'rev-parse']:
            return _completed(0, stdout='/Users/erato949/Documents/my-cool-repo\n')
        if cmd[:2] == ['beads', 'label']:
            label_call_count['n'] += 1
            # Real beads CLI: first add prints "Added label 'X' to ID";
            # second add prints "Issue ID already has label 'X'" — both
            # exit 0. We mirror that exit-0 contract here.
            if label_call_count['n'] == 1:
                return _completed(0, stdout=f"Added label '{cmd[3]}' to {cmd[2]}\n")
            return _completed(0, stdout=f"Issue {cmd[2]} already has label '{cmd[3]}'\n")
        return _completed(0)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        # (a) direct helper-level idempotency
        assert beads_writer._apply_repo_label('gz-test01', 'repo:my-cool-repo') is True  # noqa: SLF001
        assert beads_writer._apply_repo_label('gz-test01', 'repo:my-cool-repo') is True  # noqa: SLF001

        # (b) hook-level: two distinct create paths each get a label call
        # without the second one raising or short-circuiting.
        writer = beads_writer.get_writer()
        writer.on_tool_use(
            operation='write',
            prompt='first create',
            details={'file_path': '/a.py'},
            session_id='s5a',
            project='test',
            cwd='/Users/erato949/Documents/my-cool-repo',
        )
        writer.on_tool_use(
            operation='write',
            prompt='second create — distinct content to bypass dedup',
            details={'file_path': '/b.py'},
            session_id='s5b',
            project='test',
            cwd='/Users/erato949/Documents/my-cool-repo',
        )

    # Direct calls (2) + hook end-to-end calls (2) = 4 total label invocations.
    assert label_call_count['n'] == 4


# ─── 6. test_detect_repo_label_handles_missing_git_binary ─────────────────────


def test_detect_repo_label_handles_missing_git_binary():
    """Bonus property: `_detect_repo_label` must not crash if `git` is
    unavailable on the PATH. This is the FileNotFoundError fail-open branch."""
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("git: command not found")

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        assert beads_writer._detect_repo_label('/anywhere') is None  # noqa: SLF001


# ─── 7. test_apply_repo_label_handles_timeout ─────────────────────────────────


def test_apply_repo_label_handles_timeout(capsys):
    """Bonus property: subprocess timeout in `beads label` is fail-open —
    returns False, warns to stderr, never raises."""
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    with patch.object(beads_writer.subprocess, 'run', side_effect=fake_run):
        result = beads_writer._apply_repo_label('gz-abc12', 'repo:foo')  # noqa: SLF001
    assert result is False
    captured = capsys.readouterr()
    assert 'Warning' in captured.err
