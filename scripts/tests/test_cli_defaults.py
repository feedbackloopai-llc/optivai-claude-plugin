"""test_cli_defaults.py - regression guard against machine-specific CLI defaults.

The runner's --repo and --branch defaults (and the last-resort verify command)
were once hardcoded to a developer's absolute path and a WIP branch. These tests
lock in machine-independent defaults (cwd for --repo, "main" for --branch) and
assert no machine-specific path string remains in the runner source. Mirrors the
Pi engine (cfg.repo ?? process.cwd(), cfg.branch ?? "main").

Run: python3 -m pytest scripts/tests/test_cli_defaults.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path
_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import loop_runner  # noqa: E402


def test_repo_default_tracks_cwd() -> None:
    args = loop_runner._build_arg_parser().parse_args(["--molecule", "x"])
    assert args.repo == os.getcwd()


def test_branch_default_is_main() -> None:
    args = loop_runner._build_arg_parser().parse_args(["--molecule", "x"])
    assert args.branch == "main"
    assert args.branch != "perf/windows-optimization"


def test_default_helpers_are_machine_independent() -> None:
    assert loop_runner._default_repo() == os.getcwd()
    assert loop_runner._default_branch() == "main"


def test_no_machine_specific_hardcodes_in_runner_source() -> None:
    src = (_SCRIPTS_DIR / "loop_runner.py").read_text(encoding="utf-8")
    assert "/Users/erato949" not in src, "machine-specific absolute path leaked into loop_runner.py"
    assert "perf/windows-optimization" not in src, "machine-specific WIP branch leaked into loop_runner.py"
