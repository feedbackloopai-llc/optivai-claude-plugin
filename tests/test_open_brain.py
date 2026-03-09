#!/usr/bin/env python3
"""
Tests for Open Brain (scripts/open_brain.py).

Unit tests for pure functions + integration tests against live PostgreSQL.
Run: python3 -m pytest tests/test_open_brain.py -v
"""

import json
import os
import sys
import subprocess
import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain


# ─── Unit Tests (no PostgreSQL needed) ────────────────────────────────────────

class TestParseArray:
    def test_none_returns_empty(self):
        assert open_brain._parse_array(None) == []

    def test_list_passthrough(self):
        assert open_brain._parse_array(["a", "b"]) == ["a", "b"]

    def test_json_string(self):
        assert open_brain._parse_array('["x", "y"]') == ["x", "y"]

    def test_empty_json_array(self):
        assert open_brain._parse_array("[]") == []

    def test_invalid_string(self):
        assert open_brain._parse_array("not json") == []

    def test_empty_string(self):
        assert open_brain._parse_array("") == []

    def test_json_object_string(self):
        # Not an array — should return empty
        assert open_brain._parse_array('{"a": 1}') == []


class TestStripMarkdownFencing:
    def test_no_fencing(self):
        assert open_brain._strip_markdown_fencing('{"a": 1}') == '{"a": 1}'

    def test_json_fencing(self):
        assert open_brain._strip_markdown_fencing('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_plain_fencing(self):
        assert open_brain._strip_markdown_fencing('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_extra_whitespace(self):
        result = open_brain._strip_markdown_fencing('  ```json\n{"a": 1}\n```  ')
        assert '"a"' in result

    def test_no_closing_fence(self):
        result = open_brain._strip_markdown_fencing('```json\n{"a": 1}')
        assert '"a"' in result


class TestGenerateThoughtId:
    def test_format(self):
        tid = open_brain._generate_thought_id()
        assert tid.startswith("brain-")
        parts = tid.split("-")
        assert len(parts) == 3
        assert parts[1].isdigit()
        assert len(parts[2]) == 8

    def test_uniqueness(self):
        ids = {open_brain._generate_thought_id() for _ in range(100)}
        assert len(ids) == 100  # All unique


class TestGetUserId:
    def test_returns_lowercase(self):
        uid = open_brain._get_user_id()
        assert uid == uid.lower()
        assert len(uid) > 0


# ─── Integration Tests (requires live PostgreSQL) ─────────────────────────────

BRAIN_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "open_brain.py"
)


def _run_brain(*args, stdin_data=None, expect_success=True):
    """Run open_brain.py CLI and return stdout."""
    cmd = [sys.executable, BRAIN_SCRIPT] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_data,
        timeout=30,
        env={**os.environ},
    )
    if expect_success:
        assert result.returncode == 0, f"Failed: {result.stderr}"
    return result.stdout.strip(), result.stderr.strip()


def _run_brain_json(*args):
    """Run with --json flag and parse output."""
    stdout, _ = _run_brain(*args, "--json")
    return json.loads(stdout)


@pytest.fixture(scope="module")
def test_thought():
    """Capture a test thought and return its data."""
    text = (
        "Integration test: Talked with Alice about the Q4 migration timeline. "
        "She said the beta is ready but we need Dave to sign off on security. "
        "Follow up with Dave by Friday."
    )
    stdout, _ = _run_brain("--capture", text, "--source", "pytest", "--project", "test")
    assert "Captured:" in stdout
    # Parse the thought_id from output
    for line in stdout.split("\n"):
        if "Captured:" in line:
            thought_id = line.split("Captured:")[1].strip()
            break
    return {"thought_id": thought_id, "text": text, "output": stdout}


class TestCaptureIntegration:
    def test_capture_returns_metadata(self, test_thought):
        out = test_thought["output"]
        assert "Type:" in out
        assert "Summary:" in out

    def test_capture_extracts_people(self, test_thought):
        out = test_thought["output"].lower()
        assert "alice" in out or "dave" in out

    def test_capture_json_format(self):
        result = _run_brain_json(
            "--capture",
            "Test thought for JSON format: considering switching to PostgreSQL for the analytics layer.",
            "--source", "pytest",
        )
        assert "thought_id" in result
        assert "type" in result
        assert "summary" in result
        assert isinstance(result.get("topics"), list)


class TestSearchIntegration:
    def test_semantic_match(self, test_thought):
        """Search with semantically related terms (not exact words)."""
        results = _run_brain_json("--search", "project timeline and deadlines")
        assert len(results) > 0
        # Our test thought about Q4 migration timeline should match
        found = any("migration" in str(r).lower() or "timeline" in str(r).lower() for r in results)
        assert found, f"Expected to find migration/timeline thought, got: {results}"

    def test_similarity_scores(self, test_thought):
        results = _run_brain_json("--search", "migration security approval")
        assert len(results) > 0
        for r in results:
            assert "SIMILARITY" in r
            assert 0 < r["SIMILARITY"] <= 1.0

    def test_empty_query(self):
        """Should still return results (everything is somewhat similar to empty)."""
        stdout, _ = _run_brain("--search", "xyzzy_nomatch_12345")
        # May or may not return results depending on threshold
        assert stdout  # at least some output

    def test_user_isolation(self, test_thought):
        """Fake user should see nothing."""
        env = {**os.environ, "USER": "pytest_fake_user_99999"}
        result = subprocess.run(
            [sys.executable, BRAIN_SCRIPT, "--search", "migration timeline", "--json"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode == 0
        results = json.loads(result.stdout.strip())
        assert len(results) == 0


class TestRecentIntegration:
    def test_recent_returns_today(self, test_thought):
        results = _run_brain_json("--recent", "--days", "1")
        assert len(results) > 0

    def test_recent_with_limit(self, test_thought):
        results = _run_brain_json("--recent", "--limit", "2")
        assert len(results) <= 2

    def test_recent_text_output(self, test_thought):
        stdout, _ = _run_brain("--recent")
        assert "Recent thoughts" in stdout


class TestStatsIntegration:
    def test_stats_returns_counts(self, test_thought):
        stdout, _ = _run_brain("--stats")
        assert "Open Brain Stats" in stdout
        assert "Total thoughts:" in stdout

    def test_stats_json(self, test_thought):
        result = _run_brain_json("--stats")
        assert "overview" in result
        assert "top_topics" in result
        assert "top_people" in result
        assert "type_distribution" in result
        assert result["overview"].get("TOTAL_THOUGHTS", 0) > 0


class TestPiBridge:
    def test_capture_via_stdin(self):
        payload = json.dumps({
            "op": "capture",
            "text": "Pi bridge test: evaluating new onboarding flow for enterprise customers.",
            "source": "pytest-pi",
            "session_id": "test-session",
            "project": "test",
        })
        stdout, _ = _run_brain("--from-pi", stdin_data=payload)
        result = json.loads(stdout)
        assert "thought_id" in result
        assert "type" in result

    def test_search_via_stdin(self, test_thought):
        payload = json.dumps({"op": "search", "query": "onboarding enterprise"})
        stdout, _ = _run_brain("--from-pi", stdin_data=payload)
        results = json.loads(stdout)
        assert isinstance(results, list)

    def test_stats_via_stdin(self, test_thought):
        payload = json.dumps({"op": "stats"})
        stdout, _ = _run_brain("--from-pi", stdin_data=payload)
        result = json.loads(stdout)
        assert "overview" in result

    def test_unknown_op(self):
        payload = json.dumps({"op": "explode"})
        stdout, _ = _run_brain("--from-pi", stdin_data=payload)
        result = json.loads(stdout)
        assert "error" in result
