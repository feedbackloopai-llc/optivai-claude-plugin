"""dispatch_gate.py — Pure dispatch-quality validator (no I/O, no stdin parsing).

Implements the three validation rules from docs/dispatch-contract.md:
  Rule 1 — Termination criterion (→ missing[] if absent; drives block in strict mode)
  Rule 2 — Redundant content paste (→ warnings[]; skipped on short prompts)
  Rule 3 — Output contract (→ warnings[] if absent)

Public API
----------
evaluate_dispatch(prompt, *, mode, min_prompt_tokens, max_embed_chars) -> dict

The returned verdict has shape:
  {
    "checked":   bool,  # False when out-of-scope (mode=off, empty prompt)
    "compliant": bool,  # True iff missing[] is empty AND warnings[] is empty
    "missing":   [str], # high-severity: Rule 1 absent
    "warnings":  [str], # low-severity nudges: Rule 2 / Rule 3
    "block":     bool,  # True only when mode=strict AND termination missing
  }

Fail-open guarantee: any unhandled exception inside evaluate_dispatch returns an
allow-verdict (checked=False, compliant=True, missing=[], warnings=[], block=False).
Callers that need env-resolved defaults should call resolve_env_config() first.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Compiled regexes (case-insensitive) — exact patterns from the spec
# ---------------------------------------------------------------------------

_TERMINATION_RE = re.compile(
    r"acceptance"
    r"|done when"
    r"|success criteri"
    r"|deliverable"
    r"|definition of done"
    r"|stop when"
    r"|when (you('re| are))? ?(complete|finished|done)"
    r"|complete when"
    r"|expected (output|result|behavior)"
    r"|must (return|produce|deliver|output)"
    r"|return (only|a |the |json|the following)"
    r"|criteria:"
    r"|verify that",
    re.IGNORECASE,
)

_OUTPUT_CONTRACT_RE = re.compile(
    r"return |report|output|summary|respond with|provide (a|the)|hand back|deliver(able)?|produce (a|the)",
    re.IGNORECASE,
)

_PATH_RE = re.compile(
    r"[\w.\-]+/[\w.\-]+\.(py|ts|tsx|js|mjs|md|sql|sh|ps1|json|ya?ml)"
    r"|\b(scripts|src|docs|tests?|hooks)/",
    re.IGNORECASE,
)

# Matches fenced code blocks (``` ... ```) — we need the inner content length
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# Env resolution helper (separate from evaluate_dispatch so the validator is pure)
# ---------------------------------------------------------------------------

def resolve_env_config() -> dict:
    """Read DISPATCH_GATE_MODE / MIN_PROMPT_TOKENS / MAX_EMBED_CHARS from env.

    Returns a dict suitable for **-unpacking into evaluate_dispatch:
      {"mode": str, "min_prompt_tokens": int, "max_embed_chars": int}
    """
    import os

    raw_mode = os.environ.get("DISPATCH_GATE_MODE", "warn").strip().lower()
    if raw_mode not in ("warn", "strict", "off"):
        raw_mode = "warn"

    try:
        min_tokens = int(os.environ.get("DISPATCH_GATE_MIN_PROMPT_TOKENS", "150"))
    except (ValueError, TypeError):
        min_tokens = 150

    try:
        max_embed = int(os.environ.get("DISPATCH_GATE_MAX_EMBED_CHARS", "1500"))
    except (ValueError, TypeError):
        max_embed = 1500

    return {
        "mode": raw_mode,
        "min_prompt_tokens": min_tokens,
        "max_embed_chars": max_embed,
    }


# ---------------------------------------------------------------------------
# Allow-verdict factory (used for fail-open and out-of-scope cases)
# ---------------------------------------------------------------------------

def _allow_verdict(checked: bool = False) -> dict:
    return {
        "checked": checked,
        "compliant": True,
        "missing": [],
        "warnings": [],
        "block": False,
    }


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def evaluate_dispatch(
    prompt,
    *,
    mode: str = "warn",
    min_prompt_tokens: int = 150,
    max_embed_chars: int = 1500,
) -> dict:
    """Evaluate a subagent dispatch prompt against the contract rules.

    Parameters
    ----------
    prompt:
        The dispatch prompt string.  Non-str / None inputs are treated as
        empty (checked=False, allow).
    mode:
        "warn" (default) — advisory only, never sets block=True.
        "strict"         — sets block=True when termination criterion missing.
        "off"            — passthrough; returns checked=False always.
    min_prompt_tokens:
        Estimated token count below which Rule 2 (redundant paste) is skipped.
        Approximated as len(prompt) // 4.
    max_embed_chars:
        Fenced-block inner-content length threshold for Rule 2.

    Returns
    -------
    Verdict dict:  {checked, compliant, missing, warnings, block}
    """
    try:
        return _evaluate_dispatch_inner(prompt, mode=mode,
                                        min_prompt_tokens=min_prompt_tokens,
                                        max_embed_chars=max_embed_chars)
    except Exception:
        # Fail-open: any internal error → allow
        return _allow_verdict(checked=False)


def _evaluate_dispatch_inner(
    prompt,
    *,
    mode: str,
    min_prompt_tokens: int,
    max_embed_chars: int,
) -> dict:
    """Inner implementation — may raise; caller wraps in try/except."""

    # --- mode=off → skip entirely -----------------------------------------
    if mode == "off":
        return _allow_verdict(checked=False)

    # --- type coercion: non-str → treat as empty --------------------------
    if not isinstance(prompt, str):
        return _allow_verdict(checked=False)

    # --- empty or trivially short prompt → allow (not a dispatch) ---------
    if not prompt.strip():
        return _allow_verdict(checked=False)

    # We are going to check this prompt.
    missing: list[str] = []
    warnings: list[str] = []

    # --- Rule 1: Termination criterion ------------------------------------
    if not _TERMINATION_RE.search(prompt):
        missing.append(
            "termination criterion missing: add an explicit acceptance/done condition "
            "(e.g. 'Acceptance: …', 'Done when …', 'Return only …')."
        )

    # --- Rule 2: Redundant content paste (skipped on short prompts) -------
    estimated_tokens = len(prompt) // 4
    if estimated_tokens >= min_prompt_tokens:
        _check_redundant_paste(prompt, max_embed_chars, warnings)

    # --- Rule 3: Output contract ------------------------------------------
    if not _OUTPUT_CONTRACT_RE.search(prompt):
        warnings.append(
            "Output contract absent: specify what the subagent should output or return "
            "(e.g. 'Return a one-paragraph summary', 'Report the files changed')."
        )

    # --- Assemble verdict -------------------------------------------------
    compliant = (len(missing) == 0 and len(warnings) == 0)
    block = (mode == "strict" and len(missing) > 0)

    return {
        "checked": True,
        "compliant": compliant,
        "missing": missing,
        "warnings": warnings,
        "block": block,
    }


def _check_redundant_paste(prompt: str, max_embed_chars: int, warnings: list) -> None:
    """Rule 2 — warn if a large fenced block co-exists with a path reference.

    Modifies *warnings* in-place.  Only fires when BOTH conditions hold:
      1. At least one fenced block whose inner content length >= max_embed_chars
      2. The prompt also contains a file-path reference matching _PATH_RE

    Embedding a target spec or test inline is legitimate; this only flags the
    redundant case where a large paste co-exists with a path reference.
    """
    # Find the largest fenced-block inner content
    largest_inner = 0
    for m in _FENCED_BLOCK_RE.finditer(prompt):
        inner = m.group(1)
        if len(inner) > largest_inner:
            largest_inner = len(inner)

    if largest_inner < max_embed_chars:
        return  # No large block — no warning

    # Large block present; only warn if a path reference is also present
    if not _PATH_RE.search(prompt):
        return  # No path reference — embedding may be intentional (spec/test)

    warnings.append(
        f"Redundant content paste: fenced block contains {largest_inner} chars of embedded content "
        f"but a file path is already referenced. Pass the path instead of pasting the content; "
        f"the subagent will read it directly."
    )
