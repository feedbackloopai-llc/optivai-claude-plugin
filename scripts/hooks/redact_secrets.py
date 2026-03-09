#!/usr/bin/env python3
"""
ABOUTME: Secret redaction utility for Claude Code hooks.
ABOUTME: Filters API keys, tokens, and credentials before logging/storing.

Provides a centralized redaction function used by:
- beads_writer.py (auto-created beads)
- log_writer.py (JSONL activity logs)
- memory_writer.py (memory system)

Patterns are ordered from most specific to most general to avoid
over-redaction while still catching sensitive data.
"""

import re
from typing import List, Tuple

# Redaction placeholder
REDACTED = "[REDACTED]"

# High-priority patterns: ALWAYS redacted, never skipped
# These are unambiguously secrets based on context (e.g., "Bearer", "Authorization")
HIGH_PRIORITY_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    # Bearer tokens in auth headers (always a secret, regardless of format)
    ("bearer_token", re.compile(r'(Bearer\s+)([a-zA-Z0-9_.-]+(?:-[a-zA-Z0-9_.-]+)*)', re.IGNORECASE), r'\1' + REDACTED),

    # Basic auth (base64 encoded user:pass)
    ("basic_auth", re.compile(r'(Basic\s+)([A-Za-z0-9+/]{10,}={0,2})', re.IGNORECASE), r'\1' + REDACTED),

    # Authorization header with any token
    ("auth_header", re.compile(r'(Authorization["\']?\s*[:=]\s*["\']?)([^"\'>\s]{10,})(["\']?)', re.IGNORECASE), r'\1' + REDACTED + r'\3'),

    # Password/secret parameters (context makes it clear it's sensitive)
    ("password_param", re.compile(r'(["\']?(?:password|passwd|pwd|secret|private_key|privatekey)["\']?\s*[:=]\s*["\']?)([^\s"\']{6,})(["\']?)', re.IGNORECASE), r'\1' + REDACTED + r'\3'),

    # API key/token parameters (context makes it clear)
    ("api_key_param", re.compile(r'(["\']?(?:api[_-]?key|apikey|access[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret)["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_.-]{12,})(["\']?)', re.IGNORECASE), r'\1' + REDACTED + r'\3'),

    # RSA private key content
    ("private_key", re.compile(r'-----BEGIN[A-Z ]+PRIVATE KEY-----[\s\S]*?-----END[A-Z ]+PRIVATE KEY-----'), REDACTED),
]

# Standard patterns: Redacted unless matched by a skip pattern
SECRET_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    # Payment/billing API keys (sk_live_, pk_test_ format)
    ("payment_key", re.compile(r'\b(sk|pk|rk)_(live|test)_[a-zA-Z0-9]{20,}'), REDACTED),

    # AWS Access Key IDs
    ("aws_key", re.compile(r'\bAKIA[0-9A-Z]{16}\b'), REDACTED),

    # Regional PAT tokens (pat-region-uuid format)
    ("regional_pat", re.compile(r'pat-na1-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', re.IGNORECASE), REDACTED),

    # Atlassian/JIRA API tokens (ATATT prefix, very long)
    ("atlassian_token", re.compile(r'ATATT[a-zA-Z0-9_-]{50,}'), REDACTED),

    # JWT tokens (three base64 segments separated by dots)
    ("jwt_token", re.compile(r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+'), REDACTED),

    # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    ("github_token", re.compile(r'\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}'), REDACTED),

    # Slack tokens
    ("slack_token", re.compile(r'\bxox[baprs]-[a-zA-Z0-9-]{10,}'), REDACTED),

    # UUID-format API tokens (token/key = uuid)
    ("uuid_api_token", re.compile(r'(?:token|key)["\']?\s*[:=]\s*["\']?([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', re.IGNORECASE), REDACTED),

    # Generic long hex strings (32+ chars, likely API keys or hashes used as secrets)
    ("long_hex", re.compile(r'(?<![/a-f0-9])[a-f0-9]{32,}(?![/a-f0-9])'), REDACTED),

    # Generic long alphanumeric that looks like API key (40+ chars, mixed case/numbers)
    ("long_alphanum", re.compile(r'(?<![a-zA-Z0-9])(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])[A-Za-z0-9]{40,}(?![a-zA-Z0-9])'), REDACTED),
]

# Patterns to explicitly SKIP (false positives) - only applies to standard patterns
SKIP_PATTERNS: List[re.Pattern] = [
    # Git commit hashes (40 hex chars but in git context)
    re.compile(r'(?:commit|merge|rebase|cherry-pick|checkout)\s+[a-f0-9]{7,40}', re.IGNORECASE),
    # File hashes in paths
    re.compile(r'/[a-f0-9]{32,}/'),
    # Standalone UUIDs (not in sensitive context) - common IDs, not secrets
    re.compile(r'(?:id|uuid|guid)["\']?\s*[:=]\s*["\']?[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', re.IGNORECASE),
    # Session IDs (our own logging)
    re.compile(r'session-\d{8}-\d{6}-[a-f0-9]{8}', re.IGNORECASE),
]


def _matches_skip_pattern(text: str, match_start: int, match_end: int) -> bool:
    """Check if a match position overlaps with a skip pattern."""
    for skip_pattern in SKIP_PATTERNS:
        for skip_match in skip_pattern.finditer(text):
            # Check for overlap
            if not (match_end <= skip_match.start() or match_start >= skip_match.end()):
                return True
    return False


def redact_secrets(text: str) -> str:
    """
    Redact secrets from text.

    Args:
        text: Input text that may contain secrets

    Returns:
        Text with secrets replaced by [REDACTED]
    """
    if not text or not isinstance(text, str):
        return text

    result = text

    # First pass: High-priority patterns (always applied, no skip check)
    for name, pattern, replacement in HIGH_PRIORITY_PATTERNS:
        result = pattern.sub(replacement, result)

    # Second pass: Standard patterns (check skip patterns)
    for name, pattern, replacement in SECRET_PATTERNS:
        matches = list(pattern.finditer(result))

        # Process in reverse to preserve positions
        for match in reversed(matches):
            if not _matches_skip_pattern(text, match.start(), match.end()):
                if '\\1' in replacement:
                    before = result[:match.start()]
                    after = result[match.end():]
                    replaced = pattern.sub(replacement, match.group(0))
                    result = before + replaced + after
                else:
                    result = result[:match.start()] + replacement + result[match.end():]

    return result


def redact_dict(data: dict, max_depth: int = 10) -> dict:
    """
    Recursively redact secrets from a dictionary.

    Args:
        data: Dictionary that may contain secrets in values
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        Dictionary with secrets redacted from string values
    """
    if max_depth <= 0:
        return data

    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = redact_secrets(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, max_depth - 1)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item, max_depth - 1) if isinstance(item, dict)
                else redact_secrets(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value

    return result


# Test when run directly
if __name__ == "__main__":
    test_cases = [
        # High-priority (context-based)
        ("Authorization: Bearer ea52e830-2381-11ef-9f82-cfa6524f3f46", "Bearer token (UUID format)"),
        ("Authorization: Bearer abc123def456ghi789", "Bearer token (alphanumeric)"),
        ('{"password": "YvKzsSGE2ti3!5%gFz$3"}', "Password in JSON"),
        ("password=YvKzsSGE2ti3!5%gFz$3", "Password param"),
        ('{"api_key": "c3fc81ec1f9de2f36017bd9dc17a97c14888e024"}', "API key in JSON"),

        # Specific token formats (constructed dynamically to avoid push protection false positives)
        (f"Payment key: {'sk' + '_live_' + 'x' * 40}", "Payment API key"),
        (f"Regional PAT: {'pat' + '-na1-' + '00000000-0000-0000-0000-000000000000'}", "Regional PAT token"),
        (f"JIRA token: {'ATATT3' + 'x' * 60}", "Atlassian token"),
        ("Token: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.dummysig", "JWT token"),
        (f"{'ghp_' + 'x' * 36}", "GitHub token"),

        # Should NOT redact
        ("git commit abc123def456 merged", "Git commit (preserve)"),
        ('{"id": "f61bee3e-3743-47b5-a6f8-31b054b015f3"}', "UUID id field (preserve)"),
        ("session-20250205-143215-abc12345", "Session ID (preserve)"),
        ("Normal text without secrets", "Normal text"),
    ]

    print("Secret Redaction Tests")
    print("=" * 60)

    for text, description in test_cases:
        redacted = redact_secrets(text)
        changed = text != redacted
        status = "REDACTED" if changed else "unchanged"
        print(f"\n{description} [{status}]:")
        print(f"  In:  {text[:70]}{'...' if len(text) > 70 else ''}")
        print(f"  Out: {redacted[:70]}{'...' if len(redacted) > 70 else ''}")
