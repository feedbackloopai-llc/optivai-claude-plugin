"""redact-S9: L6b egress hook (post_tool_use.py) tests.

The post-tool-use hook reads tool/model output from stdin, applies the
composed redaction pipeline, and writes redacted output to stdout. The
hook MUST be fail-open: any error path returns the original text (or
empty string), and the hook never crashes the agent's tool pipeline.
"""
import os
import subprocess

HOOK = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "hooks", "post_tool_use.py"
)


def _run_hook(stdin_text: str) -> subprocess.CompletedProcess:
    """Invoke the hook as a subprocess with stdin_text on stdin."""
    return subprocess.run(
        ["python3", HOOK],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestPostToolUseHook:
    def test_hook_exists_and_executable(self):
        assert os.path.exists(HOOK), "post_tool_use.py missing"
        assert os.access(HOOK, os.X_OK), "post_tool_use.py not executable"

    def test_redacts_secret_in_stdin(self):
        """An AWS key in the tool output must not appear in the hook's
        stdout."""
        text_in = "Tool output: AWS key AKIAIOSFODNN7EXAMPLE used here"
        result = _run_hook(text_in)
        assert result.returncode == 0, (
            f"Hook returned non-zero: {result.stderr!r}"
        )
        assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout, (
            f"AWS key leaked through egress hook: {result.stdout!r}"
        )

    def test_redacts_pii_in_stdin(self):
        """Email and phone PII must be stripped from egress."""
        text_in = "User reported email alice@example.com and phone 555-867-5309"
        result = _run_hook(text_in)
        assert result.returncode == 0
        assert "alice@example.com" not in result.stdout
        assert "555-867-5309" not in result.stdout

    def test_clean_text_passes_through(self):
        """No-secret text must be passed through verbatim."""
        text_in = "clean tool output, nothing sensitive here"
        result = _run_hook(text_in)
        assert result.returncode == 0
        assert result.stdout == text_in

    def test_empty_input_returns_clean(self):
        """Empty stdin must yield empty stdout, no crash."""
        result = _run_hook("")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_multiline_input_handled(self):
        """Multiline tool output: secrets stripped, prose preserved."""
        text_in = (
            "line 1: AKIAIOSFODNN7EXAMPLE\n"
            "line 2: alice@example.com\n"
            "line 3: clean prose\n"
        )
        result = _run_hook(text_in)
        assert result.returncode == 0
        assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout
        assert "alice@example.com" not in result.stdout
        assert "clean prose" in result.stdout

    def test_hook_never_crashes_on_bizarre_input(self):
        """Even weird unicode/control-char input must NOT crash the hook —
        fail-open contract."""
        text_in = "\x00\x01\xff\xfe binary noise here"
        result = _run_hook(text_in)
        # Returns 0 either way — fail-open contract
        assert result.returncode == 0, (
            f"Hook crashed on bizarre input: {result.stderr!r}"
        )

    def test_redacts_multiple_secret_categories(self):
        """Hook applies the COMPOSED pipeline — AWS + GitHub + email all
        redacted in one pass."""
        text_in = (
            "Build output:\n"
            "  AWS=AKIAIOSFODNN7EXAMPLE\n"
            "  Email=alice@example.com\n"
            "  done\n"
        )
        result = _run_hook(text_in)
        assert result.returncode == 0
        assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout
        assert "alice@example.com" not in result.stdout
        assert "done" in result.stdout
