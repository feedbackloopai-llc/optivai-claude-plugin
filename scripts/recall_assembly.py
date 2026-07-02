"""T2 / fblai-am3u0: recall_assembly — pure token-budget recall assembly.

Pure and deterministic: no I/O, no DB, no network, no clock reads, no
randomness, no new model, zero per-recall API cost. Callers (auto_recall_hook
T5, context_primer T5, Pi T7b, the T4 projectors) do the I/O; this module
only transforms lists of ``search()``-shaped atom dicts (UPPERCASE keys) into
a rendered, token-budgeted block.

Concrete-contract implementation against the already-gated T1 design at
``docs/plans/2026-07-02-recall-assembly-t1-design.md``. Every constant name,
public signature, pipeline stage, and invariant below is specified there —
this module implements it exactly; it does not redesign it.

Scope: T2 only. Semantic dedup (T3, in ``open_brain.py search()``) and the
structured-output projectors (T4) are separate beads and are NOT implemented
here. ``project_fields`` is exported for T4 to consume later.
"""
from __future__ import annotations

from typing import Any, Dict, List

# ─── §1.1 Module constants (exact names — T2 gate) ───────────────────────────

DEFAULT_RECALL_TOKEN_BUDGET = 600     # env override read by CALLERS: OPEN_BRAIN_RECALL_TOKEN_BUDGET
SUMMARY_MIN_CHARS = 80                # per-atom summary floor for budget-derived caps
SENTENCE_BOUNDARY_CHARS = (".", "!", "?")   # boundary = one of these followed by space, or a newline
ELLIPSIS = "…"                        # 1 char; appended on any truncation
SHORT_ID_CHARS = 8                    # mirrors auto_recall_hook.py:51 (last 8 of THOUGHT_ID)
EXPAND_HINT = "↳ expand any memory: python3 open_brain.py --inspect <id>"
CHARS_PER_TOKEN_ESTIMATE = 4          # fail-open fallback divisor

# Lazy module-level singleton tiktoken encoder (§2.1). Built on first use;
# left None forever in environments without tiktoken installed, in which
# case count_tokens() falls back to the char/4 estimate on every call.
_encoder: Any = None


# ─── §1.2 count_tokens ────────────────────────────────────────────────────────


def count_tokens(text: str) -> int:
    """tiktoken cl100k_base count; on ANY import/encode failure return
    ``max(1, len(text) // 4)``. Lazy module-level singleton encoder; never
    raises.
    """
    global _encoder
    try:
        if _encoder is None:
            import tiktoken  # optional dependency; guarded lazy import

            _encoder = tiktoken.get_encoding("cl100k_base")
        return len(_encoder.encode(text))
    except Exception:
        return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


# ─── §2.4 summarize_to (sentence-aware truncation) ────────────────────────────


def summarize_to(text: str, max_chars: int) -> str:
    """Sentence-boundary-aware truncation.

    ``len(result) <= max_chars`` always (for ``max_chars >= 2``). Input whose
    length is already ``<= max_chars`` is returned unchanged (no ellipsis).
    Never raises.
    """
    if len(text) <= max_chars:
        return text
    if max_chars < 2:
        return text[:max_chars]

    # Reserve 1 char for ELLIPSIS.
    prefix = text[: max_chars - 1]
    boundary_idx = -1
    for i in range(len(prefix) - 1, -1, -1):
        ch = prefix[i]
        if ch == "\n":
            boundary_idx = i
            break
        if ch in SENTENCE_BOUNDARY_CHARS and (
            i == len(prefix) - 1 or prefix[i + 1] == " "
        ):
            boundary_idx = i
            break

    if boundary_idx >= 0:
        cut = prefix[: boundary_idx + 1]
    else:
        cut = prefix  # no boundary in the prefix -> hard cut

    return cut.rstrip() + ELLIPSIS


# ─── §1.2 / §1.6 project_fields ───────────────────────────────────────────────


def project_fields(record: Dict[str, Any], keep: List[str]) -> Dict[str, Any]:
    """Field projection. Returns a NEW dict (input never mutated):

    - keys in ``keep``: copied verbatim
    - keys NOT in ``keep`` whose value is a list or dict: collapsed to
      ``f"{key}_count": len(value)``
    - other non-keep keys: dropped
    - the record's id key ("THOUGHT_ID" or "thought_id"), if present, is
      ALWAYS retained even when absent from ``keep`` (CCR reversibility,
      invariant R2).
    """
    result: Dict[str, Any] = {}
    for key, value in record.items():
        if key in keep:
            result[key] = value
        elif isinstance(value, (list, dict)):
            result[f"{key}_count"] = len(value)
        # else: dropped

    for id_key in ("THOUGHT_ID", "thought_id"):
        if id_key in record and id_key not in result:
            result[id_key] = record[id_key]

    return result


# ─── §1.4 empty-result helper ─────────────────────────────────────────────────


def _empty_result() -> Dict[str, Any]:
    return {
        "lines": [],
        "rendered": "",
        "included": 0,
        "dropped": 0,
        "dropped_ids": [],
        "token_count": 0,
        "expand_hint": EXPAND_HINT,
        "annotations": {},
    }


# ─── §1.5 atom line rendering ──────────────────────────────────────────────────


def _fixed_prefix(atom: Dict[str, Any], thought_id: str) -> str:
    """Everything in the rendered line except the summary text itself."""
    created_at_raw = atom.get("CREATED_AT")
    created_at10 = str(created_at_raw)[:10] if created_at_raw else ""
    thought_type = atom.get("THOUGHT_TYPE") or ""
    short_id = str(thought_id)[-SHORT_ID_CHARS:]

    dup_raw = atom.get("NEAR_DUPLICATE_COUNT") or 0
    # Coerce to int: T3 sets an int, but a non-numeric value must not raise `> 0`
    # (fail-open, §5) and must render without a trailing `.0`.
    dup_count = int(dup_raw) if isinstance(dup_raw, (int, float)) else 0
    dup = f" (+{dup_count} similar)" if dup_count > 0 else ""

    flags = ""
    if atom.get("LOW_CONFIDENCE"):
        flags += " [low-conf]"
    if "DISPUTED" in atom:
        flags += " [disputed]"
    if "SUPERSEDED_BY" in atom:
        flags += " [superseded]"

    return f"- {created_at10} | {thought_type} | {short_id}{dup}{flags} - "


def _summary_text(atom: Dict[str, Any]) -> str:
    # Coerce to str so a non-string SUMMARY/RAW_TEXT (outside the search()-shaped
    # contract) cannot raise inside assembly - fail-open (§5); T5 is the outer net.
    return str(atom.get("SUMMARY") or atom.get("RAW_TEXT") or "(no summary)")


def _exact_block_cost(lines: List[str], candidate_line: str) -> int:
    """The EXACT token cost of the prospective final ``rendered`` string if
    ``candidate_line`` were appended to ``lines`` right now (§1.4 format:
    ``"\\n".join(lines) + "\\n" + EXPAND_HINT``).

    B1 ("never exceed budget") is checked against ``token_count =
    count_tokens(rendered)`` on the ACTUAL final string (§1.4), not against a
    sum of independently-measured per-line costs. A tokenizer's token count is
    not exactly additive across concatenation (BPE merges — and, in fallback
    mode, integer-division floor — can shift the whole-string count by a
    token or two relative to the sum of the parts' counts). Measuring the
    real prospective string here, every time, makes B1 an exact invariant
    rather than a usually-true approximation; it is still the same greedy,
    order-preserving, first-fit-by-rank algorithm §2.2 describes.
    """
    block = "\n".join(lines + [candidate_line]) + "\n" + EXPAND_HINT
    return count_tokens(block)


def _force_fit(fixed_prefix: str, summary_raw: str, token_budget: int):
    """Binary search the largest char cap whose rendered line (plus the
    trailing hint) fits ``token_budget`` exactly (§2.3 top-atom guarantee:
    floor OVERRIDDEN downward if needed). The top atom is always index 0, so
    ``lines`` is always empty when this is called.

    Returns (forced_summary, forced_line, fits: bool).
    """
    lo, hi = 0, len(summary_raw)
    best_summary = ""
    best_line = fixed_prefix
    fits_any = False
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate_summary = summarize_to(summary_raw, mid)
        candidate_line = fixed_prefix + candidate_summary
        cost = _exact_block_cost([], candidate_line)
        if cost <= token_budget:
            best_summary = candidate_summary
            best_line = candidate_line
            fits_any = True
            lo = mid + 1
        else:
            hi = mid - 1
    return best_summary, best_line, fits_any


# ─── §1.3 / §2.2 / §2.3 assemble_recall ────────────────────────────────────────


def assemble_recall(
    atoms: List[Dict[str, Any]], token_budget: int = DEFAULT_RECALL_TOKEN_BUDGET
) -> Dict[str, Any]:
    """Relevance-ordered greedy token-fill over ``search()``-shaped atom dicts
    (UPPERCASE keys). Order-preserving: NEVER re-ranks. Returns the assembly
    result dict (§1.4).
    """
    # Stage 1: validate / fail-open.
    if not isinstance(atoms, list) or len(atoms) == 0:
        return _empty_result()
    if token_budget <= 0:
        return _empty_result()

    malformed_dropped = 0
    prepared = []  # list of (thought_id, atom)
    for atom in atoms:
        if not isinstance(atom, dict):
            malformed_dropped += 1
            continue
        thought_id = atom.get("THOUGHT_ID")
        if not thought_id:
            malformed_dropped += 1
            continue
        prepared.append((thought_id, atom))

    if not prepared:
        result = _empty_result()
        result["dropped"] = malformed_dropped
        return result

    # Stage 2: reserve the CCR hint.
    hint_tokens = count_tokens(EXPAND_HINT)
    available = token_budget - hint_tokens

    # Stage 3: relevance-ordered greedy fill (never re-sorted).
    lines: List[str] = []
    dropped_ids: List[str] = []
    annotations: Dict[str, Dict[str, Any]] = {}
    used = 0

    for idx, (thought_id, atom) in enumerate(prepared):
        is_top = idx == 0
        fixed_prefix = _fixed_prefix(atom, thought_id)
        summary_raw = _summary_text(atom)
        dup_count = int(atom.get("NEAR_DUPLICATE_COUNT") or 0)
        dup_ids = list(atom.get("NEAR_DUPLICATE_IDS") or [])

        line_full = fixed_prefix + summary_raw
        cost_full = count_tokens(line_full + "\n")
        if _exact_block_cost(lines, line_full) <= token_budget:
            lines.append(line_full)
            used += cost_full
            annotations[thought_id] = {
                "summarized": False,
                "near_duplicate_count": dup_count,
                "near_duplicate_ids": dup_ids,
            }
            continue

        remaining_tokens = available - used
        cap = max(
            SUMMARY_MIN_CHARS,
            remaining_tokens * CHARS_PER_TOKEN_ESTIMATE - len(fixed_prefix),
        )
        summary_capped = summarize_to(summary_raw, cap)
        line_capped = fixed_prefix + summary_capped
        cost_capped = count_tokens(line_capped + "\n")
        if _exact_block_cost(lines, line_capped) <= token_budget:
            lines.append(line_capped)
            used += cost_capped
            annotations[thought_id] = {
                "summarized": True,
                "near_duplicate_count": dup_count,
                "near_duplicate_ids": dup_ids,
            }
            continue

        if is_top:
            # §2.3 top-atom guarantee: force-fit, floor overridden downward.
            forced_summary, forced_line, fits_any = _force_fit(
                fixed_prefix, summary_raw, token_budget
            )
            if fits_any:
                lines.append(forced_line)
                used += count_tokens(forced_line + "\n")
                annotations[thought_id] = {
                    "summarized": True,
                    "near_duplicate_count": dup_count,
                    "near_duplicate_ids": dup_ids,
                }
                continue
            # Degenerate budget: not even the top atom fits. Per §2.3,
            # "the empty result is returned rather than a budget violation."
            # §2.3-reading: `dropped` counts only malformed atoms here (the §1.4
            # "dropped = all non-included" clause conflicts at sub-hint budgets;
            # §2.5 clamps real callers to >= 50, so this branch is unreachable
            # in practice - the choice is documented rather than behaviour-changed).
            result = _empty_result()
            result["dropped"] = malformed_dropped
            return result

        # Not the top atom, and it doesn't fit even at the floor: drop and
        # keep scanning (a later, shorter, lower-ranked atom may still fit).
        dropped_ids.append(thought_id)

    included = len(lines)
    if included == 0:
        result = _empty_result()
        result["dropped"] = malformed_dropped + len(dropped_ids)
        return result

    rendered = "\n".join(lines) + "\n" + EXPAND_HINT
    token_count = count_tokens(rendered)

    return {
        "lines": lines,
        "rendered": rendered,
        "included": included,
        "dropped": malformed_dropped + len(dropped_ids),
        "dropped_ids": dropped_ids,
        "token_count": token_count,
        "expand_hint": EXPAND_HINT,
        "annotations": annotations,
    }
