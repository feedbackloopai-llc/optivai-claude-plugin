"""Recognizer exports — secrets (S3) and PII (S4) are real after Bundle B.
EntropyRedactor (S5) and ContextRedactor (S6) remain stubs until those bundles land."""

from .secrets import secrets_redactors
from .pii import pii_redactors


class EntropyRedactor:
    """Stub replaced by recognizers/entropy.py in redact-S5."""

    def __init__(self, **kwargs):
        pass

    def detect(self, text):
        return []


class ContextRedactor:
    """Stub replaced by recognizers/context.py in redact-S6."""

    def __init__(self, inner=None, **kwargs):
        self.inner = inner

    def detect(self, text):
        return self.inner.detect(text) if self.inner else []
