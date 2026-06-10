#!/usr/bin/env python3
"""
TDD tests for context_primer.enrich_with_brain_context.

Tests cover:
  - Happy path: mock subprocess returns valid JSON for both calls
  - Subprocess failure: nonzero exit → empty string (fail-open)
  - JSON parse failure → empty string (fail-open)
  - TimeoutExpired → empty string (fail-open)
  - Deduplication by atom id across both calls
  - Total cap at 15 atoms

Run from repo root:
  python3 -m pytest scripts/hooks/tests/test_context_primer_brain.py -v
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the hooks directory is importable
_HOOKS_DIR = Path(__file__).resolve().parent.parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from context_primer import enrich_with_brain_context  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_atom(atom_id: str, summary: str = "summary text", thought_type: str = "insight") -> dict:
    """Return a dict shaped like open_brain.py JSON output (UPPERCASE keys)."""
    return {
        "THOUGHT_ID": atom_id,
        "SUMMARY": summary,
        "THOUGHT_TYPE": thought_type,
        "CREATED_AT": "2026-06-10T00:00:00+00:00",
    }


def _proc_ok(atoms: list) -> MagicMock:
    """Return a mock CompletedProcess that emits atoms as JSON stdout."""
    m = MagicMock()
    m.stdout = json.dumps(atoms)
    m.returncode = 0
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnrichWithBrainContext:
    """Tests for enrich_with_brain_context(working_dir, days, recall_k)."""

    def test_enrich_returns_block_on_happy_path(self):
        """Both subprocess calls succeed → returns a non-empty markdown block."""
        recent_atoms = [_make_atom("id-r1", "Recent atom one")]
        search_atoms = [_make_atom("id-s1", "Search atom one")]

        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _proc_ok(recent_atoms),
                _proc_ok(search_atoms),
            ]
            result = enrich_with_brain_context("/some/project/dir")

        assert result != ""
        assert "## Recent neurosymbolic context" in result
        assert "id-r1" in result
        assert "id-s1" in result
        assert "Recent atom one" in result
        assert "Search atom one" in result

    def test_enrich_returns_empty_on_subprocess_failure(self):
        """Nonzero exit on --recent call → empty string, no exception raised."""
        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["python3", "open_brain.py"]
            )
            result = enrich_with_brain_context("/some/project/dir")

        assert result == ""

    def test_enrich_returns_empty_on_json_parse_failure(self):
        """Non-JSON stdout on --recent call → empty string, no exception raised."""
        bad_proc = MagicMock()
        bad_proc.stdout = "this is not json {{{"
        bad_proc.returncode = 0

        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.return_value = bad_proc
            result = enrich_with_brain_context("/some/project/dir")

        assert result == ""

    def test_enrich_returns_empty_on_timeout(self):
        """TimeoutExpired on --recent call → empty string, no exception raised."""
        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["python3", "open_brain.py"], timeout=8
            )
            result = enrich_with_brain_context("/some/project/dir")

        assert result == ""

    def test_enrich_dedupes_overlapping_ids(self):
        """Atom present in both recent and search lists → appears exactly once."""
        shared_atom = _make_atom("shared-id", "Shared atom summary")
        # Both calls return the same atom id
        recent_atoms = [shared_atom]
        search_atoms = [shared_atom, _make_atom("unique-s", "Unique search atom")]

        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _proc_ok(recent_atoms),
                _proc_ok(search_atoms),
            ]
            result = enrich_with_brain_context("/some/project/dir")

        # shared-id must appear exactly once
        assert result.count("shared-id") == 1
        # The unique search atom should still appear
        assert "unique-s" in result

    def test_enrich_caps_total_atoms(self):
        """Combined atoms > 15 → output contains at most 15 distinct atom ids."""
        # 10 recent + 10 search = 20 total; cap is 15
        recent_atoms = [_make_atom(f"r-{i}", f"Recent {i}") for i in range(10)]
        search_atoms = [_make_atom(f"s-{i}", f"Search {i}") for i in range(10)]

        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _proc_ok(recent_atoms),
                _proc_ok(search_atoms),
            ]
            result = enrich_with_brain_context("/some/project/dir")

        # Count distinct atom ids present in the output
        all_ids = [f"r-{i}" for i in range(10)] + [f"s-{i}" for i in range(10)]
        present = [aid for aid in all_ids if aid in result]
        assert len(present) <= 15, (
            f"Expected at most 15 atom ids but found {len(present)}: {present}"
        )

    def test_enrich_search_failure_still_returns_recent_block(self):
        """If --recent succeeds but --search fails, the recent block is still returned."""
        recent_atoms = [_make_atom("r-only", "Only from recent")]

        def side_effect(cmd, **kwargs):
            # First call (--recent) succeeds; second call (--search) fails
            if "--recent" in cmd:
                return _proc_ok(recent_atoms)
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)

        with patch("context_primer.subprocess.run", side_effect=side_effect):
            result = enrich_with_brain_context("/some/project/dir")

        # Should still contain the recent atom
        assert "r-only" in result

    def test_enrich_search_timeout_still_returns_recent_block(self):
        """If --search times out but --recent succeeds, recent block is returned."""
        recent_atoms = [_make_atom("r-timeout", "Recent despite search timeout")]

        def side_effect(cmd, **kwargs):
            if "--recent" in cmd:
                return _proc_ok(recent_atoms)
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=8)

        with patch("context_primer.subprocess.run", side_effect=side_effect):
            result = enrich_with_brain_context("/some/project/dir")

        assert "r-timeout" in result

    def test_enrich_empty_results_returns_empty_string(self):
        """Both calls return empty list → enrich returns empty string (not a block)."""
        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _proc_ok([]),
                _proc_ok([]),
            ]
            result = enrich_with_brain_context("/some/project/dir")

        assert result == ""

    def test_enrich_basename_used_as_search_query(self):
        """The basename of working_dir is passed as the --search query."""
        captured_cmds = []

        def side_effect(cmd, **kwargs):
            captured_cmds.append(cmd)
            return _proc_ok([])

        with patch("context_primer.subprocess.run", side_effect=side_effect):
            enrich_with_brain_context("/users/someone/dev/my-project")

        # Second call should include the basename
        assert len(captured_cmds) == 2
        search_cmd = captured_cmds[1]
        assert "my-project" in search_cmd

    def test_enrich_timeout_parameter_passed_to_subprocess(self):
        """_run_open_brain is called with timeout=8."""
        with patch("context_primer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["python3", "open_brain.py"], timeout=8
            )
            # Should not raise
            result = enrich_with_brain_context("/some/dir")

        assert result == ""
        # Verify timeout was passed
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("timeout") == 8
