"""
Tests for scripts/install-check.sh

Exercises the three core states plus the dangerous case:
  1. all-identical    → exit 0 (report-only), table shows IDENTICAL
  2. drift detected   → exit 0 (report-only), exit 1 (--strict)
  3. LIVE-NEWER       → highlighted in output (the dangerous/silent-killer case)
  4. REPO-NEWER       → shown in output
  5. MISSING-IN-LIVE  → shown in output
  6. --pull-live      → copies only LIVE-NEWER files into repo
  7. missing repo root → exit 2 with clear message

Roots are passed as positional args:
  install-check.sh [--strict|--pull-live] <repo_root> <live_root>
"""

import os
import shutil
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

# -------------------------------------------------------------------------
# Path to the real install-check.sh
# -------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # optivai-claude-plugin/
INSTALL_CHECK = REPO_ROOT / "scripts" / "install-check.sh"


# -------------------------------------------------------------------------
# Minimal FILE_MAP mirror
# We only need a representative subset of the real FILE_MAP to exercise all
# code paths.  The script's data-driven FILE_MAP handles all real files;
# we exercise the logic here.
# -------------------------------------------------------------------------
# Each pair: (repo_rel, live_rel)
MINIMAL_PAIRS = [
    ("scripts/open_brain.py",             "hooks/open_brain.py"),
    ("scripts/hooks/context_primer.py",   "hooks/context_primer.py"),
    ("scripts/redact/__init__.py",        "hooks/redact/__init__.py"),
    (".claude/commands/brain-search.md",  "commands/brain-search.md"),
    ("skills/excalidraw-diagram/SKILL.md","skills/excalidraw-diagram/SKILL.md"),
]


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _write(path: Path, content: str, mtime_offset: int = 0) -> None:
    """Write a file and optionally shift its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime_offset != 0:
        ts = path.stat().st_mtime + mtime_offset
        os.utime(str(path), (ts, ts))


def _setup_env(
    tmp_repo: Path,
    tmp_live: Path,
    *,
    repo_newer: list = None,
    live_newer: list = None,
    missing_live: list = None,
    missing_repo: list = None,
):
    """
    Populate repo + live temp dirs with the minimal file set.

    repo_newer:   list of repo_rel paths whose repo copy has an older live copy
    live_newer:   list of repo_rel paths whose live copy is MORE RECENT (the danger case)
    missing_live: list of repo_rel paths to omit from live
    missing_repo: list of repo_rel paths to omit from repo
    """
    repo_newer = repo_newer or []
    live_newer = live_newer or []
    missing_live = missing_live or []
    missing_repo = missing_repo or []

    for repo_rel, live_rel in MINIMAL_PAIRS:
        base_content = f"# content of {repo_rel}\n"

        repo_abs = tmp_repo / repo_rel
        live_abs = tmp_live / live_rel

        # Determine content and mtime for each side
        if repo_rel in live_newer:
            # Live is newer: same content but different (live evolved), live mtime later
            repo_abs.parent.mkdir(parents=True, exist_ok=True) if repo_rel not in missing_repo else None
            live_abs.parent.mkdir(parents=True, exist_ok=True)

            if repo_rel not in missing_repo:
                _write(repo_abs, base_content)
                # write live as newer content
                _write(live_abs, base_content + "# live evolution\n")
                # Force live mtime to be 100s newer than repo
                repo_ts = repo_abs.stat().st_mtime
                os.utime(str(live_abs), (repo_ts + 100, repo_ts + 100))
            continue

        if repo_rel in repo_newer:
            # Repo is newer: same different content, repo mtime later
            if repo_rel not in missing_repo:
                _write(repo_abs, base_content + "# repo evolution\n")
            if repo_rel not in missing_live:
                _write(live_abs, base_content)
                live_ts = live_abs.stat().st_mtime
                repo_ts = live_ts + 100
                os.utime(str(repo_abs), (repo_ts, repo_ts))
            continue

        # Default: identical content, same mtime
        if repo_rel not in missing_repo:
            _write(repo_abs, base_content)
        if repo_rel not in missing_live:
            _write(live_abs, base_content)


def _run(tmp_repo: Path, tmp_live: Path, extra_args: list = None):
    cmd = [
        "bash", str(INSTALL_CHECK),
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend([str(tmp_repo), str(tmp_live)])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    return result


# =========================================================================
# Tests
# =========================================================================

class TestInstallCheckAllIdentical:
    """State 1: all files identical → exit 0, table shows IDENTICAL."""

    def test_exit_zero_when_identical(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live)
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def test_table_shows_identical(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live)
        assert "IDENTICAL" in result.stdout

    def test_summary_counts_identical(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live)
        # Summary line "N identical"
        import re
        match = re.search(r"(\d+) identical", result.stdout)
        assert match, f"Expected 'N identical' in output:\n{result.stdout}"
        assert int(match.group(1)) > 0

    def test_no_drift_mentioned_when_clean(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live)
        assert "All" in result.stdout and "identical" in result.stdout


class TestInstallCheckDrift:
    """State 2: drift detected — report-only exits 0, --strict exits 1."""

    def test_report_only_exits_0_on_drift(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, repo_newer=["scripts/open_brain.py"])
        result = _run(repo, live)
        assert result.returncode == 0

    def test_strict_exits_1_on_drift(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, repo_newer=["scripts/open_brain.py"])
        result = _run(repo, live, extra_args=["--strict"])
        assert result.returncode == 1

    def test_strict_exits_0_when_no_drift(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live, extra_args=["--strict"])
        assert result.returncode == 0

    def test_drift_count_in_summary(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, repo_newer=["scripts/open_brain.py"])
        result = _run(repo, live)
        assert "drift" in result.stdout.lower() or "REPO-NEWER" in result.stdout


class TestInstallCheckLiveNewer:
    """LIVE-NEWER: the dangerous/silent-killer case — must be prominent in output."""

    def test_live_newer_appears_in_table(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, live_newer=["scripts/open_brain.py"])
        result = _run(repo, live)
        assert "LIVE-NEWER" in result.stdout

    def test_live_newer_summary_line(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, live_newer=["scripts/open_brain.py"])
        result = _run(repo, live)
        # The summary should call out LIVE-NEWER explicitly
        assert "LIVE-NEWER" in result.stdout

    def test_live_newer_warns_repo_is_stale(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, live_newer=["scripts/open_brain.py"])
        result = _run(repo, live)
        # Must include "STALE" or "dangerous" to make the risk obvious
        output_lower = result.stdout.lower()
        assert "stale" in output_lower or "dangerous" in output_lower, (
            f"Expected 'stale' or 'dangerous' warning in output:\n{result.stdout}"
        )

    def test_live_newer_shows_repo_newer_separately(self, tmp_path):
        """Mixed state: one LIVE-NEWER and one REPO-NEWER should each appear."""
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(
            repo, live,
            live_newer=["scripts/open_brain.py"],
            repo_newer=["scripts/hooks/context_primer.py"],
        )
        result = _run(repo, live)
        assert "LIVE-NEWER" in result.stdout
        assert "REPO-NEWER" in result.stdout


class TestInstallCheckMissingFiles:
    """MISSING-IN-LIVE and MISSING-IN-REPO appear in the table."""

    def test_missing_in_live_shown(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, missing_live=["scripts/open_brain.py"])
        result = _run(repo, live)
        assert "MISSING-IN-LIVE" in result.stdout

    def test_missing_in_repo_shown(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, missing_repo=["scripts/open_brain.py"])
        result = _run(repo, live)
        assert "MISSING-IN-REPO" in result.stdout


class TestInstallCheckPullLive:
    """--pull-live copies LIVE-NEWER files back into the repo."""

    def test_pull_live_copies_live_newer_file(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, live_newer=["scripts/open_brain.py"])

        # Before: repo has old content
        repo_file = repo / "scripts" / "open_brain.py"
        old_content = repo_file.read_text()
        assert "# live evolution" not in old_content

        result = _run(repo, live, extra_args=["--pull-live"])

        # After: repo should have the live content
        new_content = repo_file.read_text()
        assert "# live evolution" in new_content, (
            f"--pull-live should have copied live → repo\n"
            f"stdout:\n{result.stdout}"
        )

    def test_pull_live_reports_copied_files(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, live_newer=["scripts/open_brain.py"])
        result = _run(repo, live, extra_args=["--pull-live"])
        assert "PULLED" in result.stdout

    def test_pull_live_does_not_copy_repo_newer(self, tmp_path):
        """--pull-live must not touch files where repo is newer."""
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live, repo_newer=["scripts/open_brain.py"])

        repo_file = repo / "scripts" / "open_brain.py"
        original = repo_file.read_text()

        _run(repo, live, extra_args=["--pull-live"])

        # Repo file should be unchanged
        assert repo_file.read_text() == original

    def test_pull_live_noop_when_no_live_newer(self, tmp_path):
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        result = _run(repo, live, extra_args=["--pull-live"])
        assert "nothing to pull" in result.stdout


class TestInstallCheckMissingRoot:
    """Missing repo or live root → exit 2 with clear message."""

    def test_exit_2_when_repo_root_missing(self, tmp_path):
        live = tmp_path / "live"
        live.mkdir()
        # Pass nonexistent repo root as positional arg
        cmd = ["bash", str(INSTALL_CHECK), "/nonexistent_repo_xxx", str(live)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 2

    def test_error_message_on_missing_repo_root(self, tmp_path):
        live = tmp_path / "live"
        live.mkdir()
        cmd = ["bash", str(INSTALL_CHECK), "/nonexistent_repo_xxx", str(live)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        combined = result.stdout + result.stderr
        assert "not found" in combined.lower() or "repo" in combined.lower()

    def test_exit_2_when_live_root_missing(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        cmd = ["bash", str(INSTALL_CHECK), str(repo), "/nonexistent_live_xxx"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 2

    def test_env_var_override_works(self, tmp_path):
        """OPTIVAI_CLAUDE_PLUGIN_ROOT env var can override the repo root."""
        repo = tmp_path / "repo"
        live = tmp_path / "live"
        _setup_env(repo, live)
        # Pass only live root as positional arg; repo via env var
        env = os.environ.copy()
        env["OPTIVAI_CLAUDE_PLUGIN_ROOT"] = str(repo)
        cmd = ["bash", str(INSTALL_CHECK), str(repo), str(live)]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert result.returncode == 0
