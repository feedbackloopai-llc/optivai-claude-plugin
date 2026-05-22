# Vendored from gz-redact on 2026-05-22 — bead redact-S1/S2.
"""Luhn checksum (mod-10) validation for credit-card-like numeric strings.

Mirrors Pi-Plugin/src/redact/validators/luhn.ts.

Spec:
  1. Strip whitespace and hyphens.
  2. Must be 13-19 digits remaining.
  3. Walk right-to-left. Double every other digit (starting with the
     second-to-rightmost). If the doubled value > 9, subtract 9.
  4. Sum all values. Result must be divisible by 10.

The function is total: any non-conforming input returns False. It never
throws on a string argument.
"""
from __future__ import annotations

import re


def is_luhn_valid(pan: str) -> bool:
    """Validate a PAN or similar numeric string using Luhn checksum.

    Args:
        pan: A string that may contain whitespace, hyphens, and digits.

    Returns:
        True if the PAN is valid per Luhn algorithm, False otherwise.
    """
    # Defensive: non-string input returns False (the function is total and
    # never raises on bad input).
    if not isinstance(pan, str):
        return False

    # Strip whitespace and hyphens.
    digits = re.sub(r"[\s-]", "", pan)

    # Must be 13-19 digits.
    if not re.match(r"^\d{13,19}$", digits):
        return False

    # Walk right-to-left, doubling every other digit.
    total = 0
    alternate = False
    for i in range(len(digits) - 1, -1, -1):
        digit = int(digits[i])
        if alternate:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        alternate = not alternate

    return total % 10 == 0


# Alias for the slim public API. The upstream gz-redact module exposes the
# function as is_luhn_valid; Open Brain v0.2.1 callers use luhn_check.
def luhn_check(pan: str) -> bool:
    """Alias for is_luhn_valid. See is_luhn_valid for full contract."""
    return is_luhn_valid(pan)
