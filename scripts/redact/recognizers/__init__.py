"""Recognizer stubs — replaced by redact-S3 (secrets), S4 (pii),
S5 (entropy), S6 (context) downstream bundles."""

secrets_redactors = []
pii_redactors = []


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
