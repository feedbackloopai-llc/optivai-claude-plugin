"""redact-S4: PII recognizer tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact import compose, redact, pii_redactors


class TestPiiRedactorsExported:
    def test_pii_redactors_is_list(self):
        assert isinstance(pii_redactors, list)
        assert len(pii_redactors) > 0


class TestEmail:
    def test_email_redacted(self):
        out = redact("contact alice@example.com", compose(pii_redactors))
        assert "alice@example.com" not in out
        assert "[REDACTED:pii.contact.email]" in out or "[EMAIL]" in out

    def test_email_with_plus_alias(self):
        out = redact("ping bob+tag@example.com today", compose(pii_redactors))
        assert "bob+tag@example.com" not in out


class TestPhone:
    def test_phone_dashed(self):
        out = redact("call 555-867-5309 anytime", compose(pii_redactors))
        assert "555-867-5309" not in out

    def test_phone_parens(self):
        out = redact("call (555) 867-5309 anytime", compose(pii_redactors))
        assert "(555) 867-5309" not in out

    def test_phone_with_country_code(self):
        out = redact("call +1 555-867-5309 anytime", compose(pii_redactors))
        assert "555-867-5309" not in out


class TestSsn:
    def test_ssn_redacted(self):
        out = redact("SSN: 123-45-6789", compose(pii_redactors))
        assert "123-45-6789" not in out
        assert "[REDACTED:pii.identity.ssn_us]" in out or "[SSN]" in out


class TestPanWithLuhn:
    def test_valid_visa_redacted(self):
        out = redact("card 4111-1111-1111-1111", compose(pii_redactors))
        assert "4111" not in out
        assert "[REDACTED:payment.card.pan]" in out or "[CARD]" in out

    def test_pan_failing_luhn_NOT_redacted_as_card(self):
        """Luhn validation prevents false positives. A 16-digit string that
        fails the Luhn check should NOT be redacted as [CARD]."""
        # 1234-5678-9012-3456 fails Luhn
        text = "Order ID 1234-5678-9012-3456 issued"
        out = redact(text, compose(pii_redactors))
        assert "[REDACTED:payment.card.pan]" not in out
        assert "[CARD]" not in out


class TestIpv4:
    def test_ipv4_redacted(self):
        out = redact("server 192.168.1.100", compose(pii_redactors))
        assert "192.168.1.100" not in out


class TestIpv6:
    def test_ipv6_redacted(self):
        # Real-ish IPv6
        out = redact("ipv6 2001:0db8:85a3:0000:0000:8a2e:0370:7334", compose(pii_redactors))
        # Either fully redacted, or significant portion gone
        assert "2001:0db8:85a3:0000:0000:8a2e:0370:7334" not in out


class TestDob:
    def test_dob_redacted_iso(self):
        # Vendored DOB pattern matches MM/DD/YYYY or MM-DD-YYYY.
        out = redact("born 03/14/1985", compose(pii_redactors))
        # Either [DOB]-style replacement appears, or the date is gone
        assert ("[REDACTED:pii.identity.dob]" in out) or ("[DOB]" in out) or ("03/14/1985" not in out)


class TestCleanText:
    def test_clean_text_passes_through(self):
        text = "hello world no PII here just plain prose"
        out = redact(text, compose(pii_redactors))
        assert out == text
