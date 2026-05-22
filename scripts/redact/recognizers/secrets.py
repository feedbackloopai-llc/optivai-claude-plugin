# Vendored from gz-redact on 2026-05-22 — bead redact-S3.
"""Secrets and authentication-material recognizers.

Mirrors Pi-Plugin/src/redact/recognizers/secrets.ts.

Taxonomy v1.1 Section 1. Each entry implements one category from the taxonomy.
All categories use replace-irreversible action; RegexRedactor emits hits with
replacement = "[REDACTED:<category>]".

Confidence tier:
  - DEFAULT_CONFIDENCE (0.95): patterns with strong specificity (prefix + length +
    character class).
  - 0.75 (medium): patterns that need context to disambiguate.
  - 0.55 (low): heuristic only (entropy recognizer, Task 13).

Patterns marked with a validator have an extra sanity check on top of the regex.

Deviations from taxonomy v1.1 Section 1 verbatim patterns are documented in the
TS source. Key deviations: AWS secret_key keyword relaxation, OpenAI key
alphanumeric-only restriction, Slack/GitHub/GitLab length relaxations, JWT first
char [eE], Bearer/Basic header extension, PEM backreference fix.
"""
from __future__ import annotations

import re

# The vendored source uses the upstream gz-redact RegexRedactor calling
# convention (category, pattern, validator, confidence). In Open Brain v0.2.1
# that signature lives on `_SingleRegexRedactor`; the public `RegexRedactor`
# is the slim tuple-based wrapper. We import the single-pattern class under
# the upstream name to keep the vendored body verbatim.
from ..regex_redactor import _SingleRegexRedactor as RegexRedactor, DEFAULT_CONFIDENCE
from ..types import Redactor

secrets_redactors: list[Redactor] = [
    # secret.aws.access_key
    # Validator: redundant with regex anchor but documents intent (ADR-2).
    RegexRedactor(
        "secret.aws.access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        lambda m: m.group(0).startswith("AKIA"),
    ),

    # secret.aws.secret_key
    # Keyword list includes 'aws_secret' shorthand. Value length {20,}
    # catches 36-char test tokens.
    RegexRedactor(
        "secret.aws.secret_key",
        re.compile(
            r"\b(?:aws_secret(?:_access_key)?|AWS_SECRET(?:_ACCESS_KEY)?)\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{20,})[\"']?",
            re.IGNORECASE,
        ),
    ),

    # secret.aws.session_token
    # Value length restored to {100,}; real AWS session tokens are 100-400 chars.
    RegexRedactor(
        "secret.aws.session_token",
        re.compile(
            r"\b(?:aws_session_token|AWS_SESSION_TOKEN)\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{100,})[\"']?",
            re.IGNORECASE,
        ),
    ),

    # secret.gcp.service_account
    # Covers both JSON ("type": "service_account") and YAML (type: service_account).
    # Negative lookbehind prevents matching when 'type' is a suffix of another identifier.
    # Confidence raised to 0.98 to take precedence over nested PEM keys.
    RegexRedactor(
        "secret.gcp.service_account",
        re.compile(
            r"(?<![A-Za-z0-9_])[\"']?type[\"']?\s*:\s*[\"']?service_account[\"']?",
            re.IGNORECASE,
        ),
        confidence=0.98,
    ),

    # secret.anthropic.api_key
    # Taxonomy pattern verbatim.
    # Confidence lowered to 0.75 so auth.bearer_header pattern takes precedence when both match.
    RegexRedactor(
        "secret.anthropic.api_key",
        re.compile(r"\bsk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{93,}\b"),
        confidence=0.75,
    ),

    # secret.openai.api_key
    # Bare sk-<token> variant uses [A-Za-z0-9]{48,}: real OpenAI legacy keys are
    # alphanumeric only (no underscores, no dashes). proj- and org- variants
    # legitimately use underscores and dashes.
    RegexRedactor(
        "secret.openai.api_key",
        re.compile(
            r"\bsk-(?:[A-Za-z0-9]{48,}|proj-[A-Za-z0-9_-]{60,}|org-[A-Za-z0-9_-]{60,})\b"
        ),
    ),

    # secret.huggingface.token
    # Length changed to {34,}; test token is 35 chars.
    RegexRedactor(
        "secret.huggingface.token",
        re.compile(r"\bhf_[A-Za-z0-9]{34,}\b"),
    ),

    # secret.github.pat
    # All length quantifiers relaxed to minimums ({36,}, {82,}); test tokens are 39 chars.
    RegexRedactor(
        "secret.github.pat",
        re.compile(
            r"\b(?:ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82,}|gh[osu]_[A-Za-z0-9]{36,})\b"
        ),
    ),

    # secret.gitlab.pat
    # Length changed to {20,}; test tokens are 28 chars.
    RegexRedactor(
        "secret.gitlab.pat",
        re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    ),

    # secret.slack.bot_token
    # Strict format: xoxb-{10-13digits}-{10-13digits}-{24alnum}.
    RegexRedactor(
        "secret.slack.bot_token",
        re.compile(r"\bxoxb-\d{10,13}-\d{10,13}-[A-Za-z0-9]{24}\b"),
    ),

    # secret.slack.user_token
    # Strict format: xoxp-{10-13digits}x3-{32hex}.
    RegexRedactor(
        "secret.slack.user_token",
        re.compile(r"\bxoxp-\d{10,13}-\d{10,13}-\d{10,13}-[a-f0-9]{32}\b"),
    ),

    # secret.slack.webhook
    # B-prefix restored on second path segment: T<teamID>/B<botID>/<secret>.
    RegexRedactor(
        "secret.slack.webhook",
        re.compile(
            r"\bhttps://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+\b"
        ),
    ),

    # secret.stripe.live_secret
    # Taxonomy pattern verbatim.
    RegexRedactor(
        "secret.stripe.live_secret",
        re.compile(r"\bsk_live_[A-Za-z0-9]{24,}\b"),
    ),

    # secret.stripe.live_publishable
    # Taxonomy pattern verbatim.
    RegexRedactor(
        "secret.stripe.live_publishable",
        re.compile(r"\bpk_live_[A-Za-z0-9]{24,}\b"),
    ),

    # secret.twilio.account_sid
    # Hex class expanded to [a-z0-9]{32}: corpus test tokens use 'x' placeholders.
    RegexRedactor(
        "secret.twilio.account_sid",
        re.compile(r"\bAC[a-z0-9]{32}\b"),
    ),

    # secret.sendgrid.api_key
    # Pattern broadened to SG.[A-Za-z0-9_-]{22,}: corpus test tokens have one dot segment.
    RegexRedactor(
        "secret.sendgrid.api_key",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{22,}\b"),
    ),

    # secret.hubspot.pat
    # Hex class changed to [a-zA-Z0-9-]{20,}: test tokens use uppercase.
    RegexRedactor(
        "secret.hubspot.pat",
        re.compile(r"\bpat-na1-[a-zA-Z0-9-]{20,}\b"),
    ),

    # secret.atlassian.api_token
    # Minimum length changed to {20,}: test tokens are 39 chars after ATATT prefix.
    RegexRedactor(
        "secret.atlassian.api_token",
        re.compile(r"\bATATT[a-zA-Z0-9_-]{20,}\b"),
    ),

    # secret.jwt
    # First char of eyJ changed to [eE] to catch JWTs with uppercase-E base64url encoding.
    # Confidence raised to 0.99 to take precedence over generic auth.bearer_header pattern.
    RegexRedactor(
        "secret.jwt",
        re.compile(
            r"\b[eE]yJ[A-Za-z0-9_-]{10,}\.[eE]yJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        confidence=0.99,
    ),

    # auth.bearer_header
    # Extended to handle both 'Bearer' and 'Basic' scheme names, and standalone
    # 'Bearer <token>' without 'Authorization:' prefix.
    RegexRedactor(
        "auth.bearer_header",
        re.compile(
            r"(?:\bAuthorization:\s*(?:Bearer|Basic)\s+|(?<![A-Za-z])Bearer\s+)([A-Za-z0-9._~+/=-]{20,})"
        ),
    ),

    # auth.basic_url
    # Taxonomy pattern verbatim.
    RegexRedactor(
        "auth.basic_url",
        re.compile(r"\bhttps?://[^/\s:@]+:[^@\s]+@[^\s]+"),
    ),

    # secret.private_key.pem
    # Backreference replaced with symmetric alternation groups. Supports RSA, EC,
    # OPENSSH, DSA, PGP, and bare PRIVATE KEY variants.
    # Uses [\s\S]*? to span line breaks within the key body (same as .* with dotall in Python).
    # Negative lookahead prevents matching inside quoted JSON/YAML values (e.g., "private_key": "-----BEGIN...").
    # Only match if END is NOT followed by optional whitespace and then a quote.
    RegexRedactor(
        "secret.private_key.pem",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |)PRIVATE KEY(?: BLOCK)?-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |PGP |)PRIVATE KEY(?: BLOCK)?-----(?![\"\\n]*[\"\\)])",
            re.MULTILINE,
        ),
        confidence=0.95,
    ),
]
