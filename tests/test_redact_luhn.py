"""redact-S1: Luhn checksum tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from redact.validators.luhn import luhn_check


class TestLuhnValidation:
    def test_valid_visa(self):
        assert luhn_check("4111111111111111") is True

    def test_invalid_visa_off_by_one(self):
        assert luhn_check("4111111111111112") is False

    def test_valid_mastercard(self):
        assert luhn_check("5555555555554444") is True

    def test_valid_amex(self):
        assert luhn_check("378282246310005") is True

    def test_handles_hyphen_separators(self):
        assert luhn_check("4111-1111-1111-1111") is True

    def test_handles_space_separators(self):
        assert luhn_check("4111 1111 1111 1111") is True

    def test_rejects_non_digits(self):
        assert luhn_check("abcd-1234-5678-9012") is False

    def test_rejects_too_short(self):
        # Less than 12 digits is too short to be any real PAN
        assert luhn_check("12345") is False
