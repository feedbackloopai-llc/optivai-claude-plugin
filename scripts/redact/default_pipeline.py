# fblai-1ybnr — shared default redaction pipeline module.
"""Canonical default redaction pipeline, shared across all callers.

Exposes a single pre-built pipeline list and a ``redact_pii`` function that
apply the same composed redaction used by open_brain.py.  Factored here so
hooks writers (memory_writer.py, beads_writer.py) and open_brain.py all use an
identical pipeline without duplicating the composition logic.

Usage::

    from redact.default_pipeline import redact_pii

    safe_text = redact_pii(raw_text)
    safe_text = redact_pii(None)  # returns None

Pipeline order (mirrors open_brain.py _REDACT_PIPELINE):
  1. secrets_redactors  — 8+ categories (AWS, Anthropic, OpenAI, GitHub, Slack,
     Stripe, JWT, PEM private keys, GCP, HuggingFace, etc.)
  2. pii_redactors      — 8 categories (email, phone, SSN, Luhn PAN, IPv4,
     IPv6 full, IPv6 compressed, DOB)
  3. ContextRedactor(EntropyRedactor) — high-entropy unknown tokens near
     password/api_key/token/secret/key/cred keywords.

The pipeline list is built ONCE at module import (immutable after that).
"""
from __future__ import annotations

from typing import Optional

from .compose import compose, redact as _redact
from .recognizers import (
    secrets_redactors,
    pii_redactors,
    EntropyRedactor,
    ContextRedactor,
)

# Built once at import time — the same composition open_brain.py uses.
DEFAULT_PIPELINE = compose([
    *secrets_redactors,
    *pii_redactors,
    ContextRedactor(
        inner=EntropyRedactor(),  # default opts (min_entropy=4.58, base confidence 0.4)
        keywords=["password", "secret", "api_key", "token", "key", "cred"],
        radius=15,
        upgraded_confidence=0.85,
        suppress_low_confidence=0.6,
    ),
])


def redact_pii(text: Optional[str]) -> Optional[str]:
    """Defense-in-depth redaction via the composed pipeline.

    Catches:
      - 8+ secret categories (AWS, Anthropic, OpenAI, GitHub, Slack, Stripe,
        JWT, PEM private keys, plus GCP, HuggingFace, GitLab, Twilio,
        SendGrid, HubSpot, Atlassian, bearer headers, basic-auth URLs)
      - 8 PII categories (email, phone, SSN, Luhn-validated PAN, IPv4,
        IPv6 full 8-group, IPv6 compressed/::, DOB)
      - High-entropy unknown tokens near password/api_key/similar keywords

    Returns None if input is None (preserves the legacy contract).
    """
    if text is None:
        return None
    return _redact(text, DEFAULT_PIPELINE)
