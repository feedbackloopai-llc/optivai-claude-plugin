"""redact-S7+S10: Open Brain integration tests for the composed pipeline +
--redact-test CLI."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain


BRAIN_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "open_brain.py")


class TestRedactPiiBackwardCompat:
    """The function name + signature preserved so callsites don't break."""

    def test_none_returns_none(self):
        assert open_brain.redact_pii(None) is None

    def test_empty_string_returns_empty(self):
        assert open_brain.redact_pii("") == ""

    def test_clean_text_passes_through(self):
        text = "hello world no PII here just plain prose"
        assert open_brain.redact_pii(text) == text

    def test_email_still_redacted(self):
        out = open_brain.redact_pii("contact alice@example.com")
        assert "alice@example.com" not in out

    def test_phone_still_redacted(self):
        out = open_brain.redact_pii("call 555-867-5309 anytime")
        assert "555-867-5309" not in out

    def test_ssn_still_redacted(self):
        out = open_brain.redact_pii("SSN: 123-45-6789")
        assert "123-45-6789" not in out


class TestNewCoverage:
    """New categories from the gz-redact pipeline that the old 4-pattern
    redactor never caught."""

    def test_aws_key_now_redacted(self):
        out = open_brain.redact_pii("creds AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in out

    def test_anthropic_key_now_redacted(self):
        # 96 chars after 'sk-ant-api03-' to satisfy the vendored pattern
        suffix = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij0123456789-AbCdEfGhIjKlMnOpQrStUv-AbCdEfGh"
        text = f"API key: sk-ant-api03-{suffix}"
        assert "sk-ant-api03" not in open_brain.redact_pii(text)

    def test_openai_proj_key_now_redacted(self):
        # 60+ chars after 'sk-proj-'
        text = "key=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwx"
        out = open_brain.redact_pii(text)
        assert "sk-proj-" not in out

    def test_github_pat_now_redacted(self):
        text = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        assert "ghp_" not in open_brain.redact_pii(text)

    def test_jwt_now_redacted(self):
        text = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.AbCdEfGhIjKl"
        assert "eyJ" not in open_brain.redact_pii(text)

    def test_pem_now_redacted(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB\n-----END RSA PRIVATE KEY-----"
        assert "BEGIN RSA PRIVATE KEY" not in open_brain.redact_pii(text)

    def test_pan_with_luhn_redacted(self):
        out = open_brain.redact_pii("card 4111-1111-1111-1111")
        assert "4111" not in out

    def test_pan_failing_luhn_NOT_redacted_as_card(self):
        """Luhn validation prevents false-positive [CARD] matches."""
        text = "Order ID 1234-5678-9012-3456 issued"
        out = open_brain.redact_pii(text)
        assert "[CARD]" not in out


class TestRedactTestCli:
    def test_cli_flag_in_help(self):
        result = subprocess.run(
            ["python3", BRAIN_SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        assert "--redact-test" in result.stdout

    def test_cli_human_output(self):
        result = subprocess.run(
            ["python3", BRAIN_SCRIPT, "--redact-test", "API: AKIAIOSFODNN7EXAMPLE"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        # Human output shows BOTH the original input and the redacted version
        # on separate lines. Verify the Redacted: line does not contain the
        # secret (the Input: line legitimately echoes the original).
        assert "Input:" in result.stdout
        assert "Redacted:" in result.stdout
        redacted_line = next(
            (ln for ln in result.stdout.splitlines() if ln.startswith("Redacted:")),
            "",
        )
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted_line
        assert "[REDACTED" in redacted_line

    def test_cli_json_output(self):
        result = subprocess.run(
            ["python3", BRAIN_SCRIPT, "--redact-test", "API: AKIAIOSFODNN7EXAMPLE", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["input"] == "API: AKIAIOSFODNN7EXAMPLE"
        assert "AKIAIOSFODNN7EXAMPLE" not in data["redacted"]
        assert data["changed"] is True

    def test_cli_clean_text_no_change(self):
        result = subprocess.run(
            ["python3", BRAIN_SCRIPT, "--redact-test", "hello world", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["changed"] is False
