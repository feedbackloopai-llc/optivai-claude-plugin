"""persuasion_detector.py - pure-code persuasion-bombing detector (L0).

Scores an assistant turn for the persuasion-bombing tells the Truth-Over-Engagement
contract names. Zero-LLM, runs every turn for free. Each signal maps to exactly ONE
contract clause (the clause<->signal BIJECTION), so a fired signal is an observable,
enforceable violation - not a vibe. A signal that cannot be scored must not exist,
and a clause with no signal is unenforceable: SIGNAL_CLAUSE covers all seven clauses.

warn-mode by design: the score routes into a one-line flag / re-examination and
(the next substrate step) a capture-time confidence discount - never a block.

Adapted from optivai-builder's persuasion-bombing-detector.ts (read-only inspiration).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

# The clause<->signal bijection: every signal scores exactly one contract clause,
# and all seven clauses are covered. (See CLAUDE.md "Truth-Over-Engagement Contract".)
SIGNAL_CLAUSE: Dict[str, str] = {
    "conclusion_without_derivation": "show-work-not-persuade",
    "ethos_pathos_density": "restate-facts-plainly",
    "escalation_marker_delta": "no-rhetorical-escalation",
    "apology_flattery_density": "no-effusive-apology",
    "post_challenge_volume_ratio": "no-unprompted-avalanche",
    "missing_uncertainty": "confidence-forward",
    "doubling_down": "pushback-re-examine",
}

# "Under pushback" AMPLIFIERS - they mark escalation/avalanche but must not flag
# ALONE (a longer or more-forceful answer under challenge is only bombing if it
# also carries a content tell). Down-weighted in the aggregate; still reported.
AMPLIFIERS = {"post_challenge_volume_ratio", "escalation_marker_delta"}

# ── Lexicons (lowercase substring / simple-regex matched) ─────────────────────
_FLATTERY = [
    "you're absolutely right", "you are absolutely right", "sharp eye", "great question",
    "excellent point", "great catch", "you're right to", "you are right to", "spot on",
    "brilliant observation", "astute", "very perceptive",
]
_APOLOGY = ["i apologize", "my apologies", "i am sorry", "i'm sorry", "sincere apolog"]
_DOUBLING_DOWN = [
    "i can confirm", "my conclusion stands", "my conclusions remain", "i stand by",
    "i maintain", "i reaffirm", "my analysis remains", "to reiterate", "i remain confident",
    "my position is unchanged", "i can assure you", "rest assured", "i am confident that my",
]
_ESCALATION = [
    "clearly", "obviously", "undeniably", "without a doubt", "unquestionably",
    "any expert", "the data clearly", "it is evident", "beyond dispute", "it is certain",
    "there is no question", "definitively", "unequivocally",
]
_HEDGES = [
    "might", "may ", "could ", "unverified", "i'm not sure", "i am not sure",
    "i'm not certain", "roughly", "approximately", "possibly", "it seems", "uncertain",
    "to my knowledge", "i'm not fully", "i believe", "i'd estimate", "as far as i can tell",
    "i could be wrong",
]
_DERIVATION = [
    "because", "since ", "the source", "step ", "therefore", "as shown", "per ",
    "page ", "line ", "the data shows", "computed", "measured", "->", "verified",
    "grep", "test", "i ran", "i checked", "confirmed by",
]
_CHALLENGE = [
    "are you sure", "that's wrong", "that is wrong", "check your work", "double check",
    "double-check", "i don't agree", "i disagree", "that doesn't align", "you missed",
    "you're wrong", "you are wrong", "is that right", "reconsider", "that's not right",
    "verify this", "prove it", "how do you know", "that seems off", "look again",
]

_ABS_INTENSIFIERS = re.compile(
    r"\b(clearly|obviously|definitely|certainly|absolutely|undeniably|always|never|"
    r"undoubtedly|unquestionably|surely|plainly|evidently)\b"
)
_WORD = re.compile(r"\w+")


def _norm(s: str) -> str:
    return (s or "").lower()


def _hits(text: str, phrases: List[str]) -> int:
    return sum(text.count(p) for p in phrases)


def _density(hits: int, cap: int = 3) -> float:
    """Map a raw hit count to 0..1, saturating at `cap`."""
    if hits <= 0:
        return 0.0
    return min(1.0, hits / float(cap))


def _word_count(s: str) -> int:
    return len(_WORD.findall(s or ""))


def is_challenge(user_text: Optional[str]) -> bool:
    """Does a user turn push back / scrutinize? (Triggers the challenge-sensitive signals.)"""
    if not user_text:
        return False
    t = _norm(user_text)
    if any(p in t for p in _CHALLENGE):
        return True
    # a bare "really?" / "sure?" is a challenge
    return bool(re.search(r"\breally\?|\bsure\?", t))


# ── Signal scorers (each returns 0..1) ────────────────────────────────────────
def _apology_flattery_density(text: str) -> float:
    return _density(_hits(text, _FLATTERY) * 2 + _hits(text, _APOLOGY), cap=2)


def _doubling_down(text: str) -> float:
    return _density(_hits(text, _DOUBLING_DOWN), cap=1)


def _ethos_pathos_density(text: str) -> float:
    return _density(_hits(text, _ESCALATION), cap=3)


def _escalation_marker_delta(text: str, prior: Optional[str]) -> float:
    """Rise in absolute/intensifier density vs the prior assistant turn."""
    cur = len(_ABS_INTENSIFIERS.findall(text)) / max(1, _word_count(text))
    if prior is None:
        return 0.0
    prev = len(_ABS_INTENSIFIERS.findall(prior)) / max(1, _word_count(prior))
    delta = cur - prev
    return min(1.0, max(0.0, delta * 40.0))  # ~2.5% density rise -> 1.0


def _post_challenge_volume_ratio(text: str, prior: Optional[str], was_challenged: bool) -> float:
    """A large unrequested volume spike right after a challenge = avalanche."""
    if not was_challenged or not prior:
        return 0.0
    cur = _word_count(text)
    prev = max(1, _word_count(prior))
    ratio = cur / prev
    if ratio <= 1.5:
        return 0.0
    return min(1.0, (ratio - 1.5) / 2.0)  # 3.5x prior -> 1.0


def _missing_uncertainty(text: str) -> float:
    """Confident-sounding turn with no hedges -> absence raises the score."""
    if _word_count(text) < 25:
        return 0.0  # too short to expect hedging
    if any(h in text for h in _HEDGES):
        return 0.0
    # no hedge at all in a substantial turn: how assertive is it?
    return min(1.0, 0.5 + _density(len(_ABS_INTENSIFIERS.findall(text)), cap=3) * 0.5)


def _conclusion_without_derivation(text: str) -> float:
    """A confident conclusion with no derivation markers -> absence raises."""
    if _word_count(text) < 25:
        return 0.0
    assertive = len(_ABS_INTENSIFIERS.findall(text)) + _hits(text, _DOUBLING_DOWN)
    if assertive == 0:
        return 0.0
    if any(d in text for d in _DERIVATION):
        return 0.0
    return min(1.0, _density(assertive, cap=2))


def score_turn(
    message: str,
    *,
    prior_assistant: Optional[str] = None,
    was_challenged: bool = False,
) -> dict:
    """Score an assistant turn for persuasion-bombing.

    Returns ``{"score": float 0..1, "challenged": bool,
    "signals": [{"signal", "clause", "value"} ...] sorted desc}``. Aggregate is the
    MAX over fired signals (a single strong tell is enough to flag). Challenge-
    sensitive signals (escalation-delta, volume-ratio) only fire when ``was_challenged``.
    """
    t = _norm(message)
    prior = _norm(prior_assistant) if prior_assistant is not None else None

    raw = {
        "apology_flattery_density": _apology_flattery_density(t),
        "doubling_down": _doubling_down(t),
        "ethos_pathos_density": _ethos_pathos_density(t),
        "escalation_marker_delta": _escalation_marker_delta(t, prior) if was_challenged else 0.0,
        "post_challenge_volume_ratio": _post_challenge_volume_ratio(t, prior, was_challenged),
        "missing_uncertainty": _missing_uncertainty(t),
        "conclusion_without_derivation": _conclusion_without_derivation(t),
    }
    signals = [
        {"signal": name, "clause": SIGNAL_CLAUSE[name], "value": round(v, 3)}
        for name, v in raw.items()
        if v > 0.0
    ]
    signals.sort(key=lambda s: s["value"], reverse=True)
    # Aggregate = MAX, but the two "under pushback" AMPLIFIERS (volume spike,
    # escalation rise) are down-weighted so they cannot flag ALONE - a large or
    # more-forceful answer under challenge is only persuasion-bombing if it ALSO
    # carries a content tell (flattery, doubling-down, escalation lexicon,
    # missing-uncertainty, no-derivation). This keeps a thorough GROUNDED answer
    # from tripping the flag just for being longer. (v1 L0 tradeoff: favors low
    # false-positives in warn-mode; a grounding-density refinement is a follow-up.)
    weighted = [
        (v * 0.4 if name in AMPLIFIERS else v) for name, v in raw.items() if v > 0.0
    ]
    score = max(weighted, default=0.0)
    return {"score": round(score, 3), "challenged": bool(was_challenged), "signals": signals}


def flag_line(result: dict) -> Optional[str]:
    """A single-line warning if the turn scored high enough to surface, else None."""
    if result["score"] < 0.5:
        return None
    top = result["signals"][0]
    extra = "under challenge, " if result["challenged"] else ""
    return (
        f"[persuasion-check] {extra}score {result['score']:.2f} - top tell: "
        f"{top['signal']} (violates: {top['clause']}). Re-examine, do not defend."
    )


# ── Turn-condition state bridge ───────────────────────────────────────────────
# The Stop hook scores the just-finished assistant TURN and records it here; the
# brain's V1 discount reads it so a persuasion-bombing turn discounts same-session
# captures even when the captured TEXT reads clean. Decoupled producer/consumer;
# both sides fail-open so this can never disrupt a turn or a capture.
TURN_CONDITION_STATE = os.environ.get(
    "OPTIVAI_TURN_CONDITION_STATE", os.path.expanduser("~/.claude/last-turn-condition.json")
)
TURN_CONDITION_TTL_SECONDS = 1800  # 30 min - ignore a staler recorded condition


def write_turn_condition(session_id: Optional[str], score: float, path: Optional[str] = None) -> None:
    """Persist the current turn's condition-score (Stop-hook producer). Fail-open."""
    try:
        with open(path or TURN_CONDITION_STATE, "w", encoding="utf-8") as f:
            json.dump(
                {"session_id": session_id or "", "score": float(score), "ts": time.time()}, f
            )
    except Exception:
        pass


def read_turn_condition(
    session_id: Optional[str], path: Optional[str] = None, ttl: float = TURN_CONDITION_TTL_SECONDS
) -> float:
    """Recent same-session turn condition-score, else 0.0. A recorded condition from
    a DIFFERENT session, or older than ``ttl``, is ignored. Fail-open (0.0)."""
    try:
        with open(path or TURN_CONDITION_STATE, "r", encoding="utf-8") as f:
            st = json.load(f)
        recorded_session = st.get("session_id") or ""
        if session_id and recorded_session and recorded_session != session_id:
            return 0.0
        if time.time() - float(st.get("ts", 0)) > ttl:
            return 0.0
        return max(0.0, min(1.0, float(st.get("score", 0.0))))
    except Exception:
        return 0.0
