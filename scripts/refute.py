#!/usr/bin/env python3
"""refute.py - an independent adversarial refuter (anti-persuasion-bombing, step 2).

A single model cannot reliably check itself. This runs a FRESH model instance with
STRUCTURALLY ISOLATED inputs - it sees only the claim under review, never the maker's
conversation history or system prompt - and prompts it to REFUTE, not bless. It must
return the strongest counter-case even when it agrees; a bare "looks good" is itself a
failure (the anti-sycophancy property).

By default it routes to a LOCAL model via Ollama, so an independent review pass costs
nothing but latency. When no local model is reachable it exits 2 so the /refute command
can escalate to a fresh cloud subagent.

Output is a schema, not prose:
  {verdict: holds|gap|broken, strongestCounterCase: str, flaws: [{claim, severity}],
   confidenceAdjustment: float}

Adapted from optivai-builder's adversarial-refuter template (read-only inspiration).
Design: the Truth-Over-Engagement contract, clause 7 (pushback-re-examine) and the
independent-review defense (a fresh mind that traces the logic by hand).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Local reasoning models, strongest-usable first; the first one present is the default.
# Override with --model or OPTIVAI_REFUTE_MODEL. gpt-oss:20b is stronger but slower.
DEFAULT_MODEL_PREFERENCE = [
    "mistral:latest",
    "llama3.1:latest",
    "gpt-oss:20b",
    "llama3.2:latest",
]

DEFAULT_BASE_URL = "http://localhost:11434"

REFUTE_SYSTEM = (
    "You are an adversarial refuter. Your ONE job is to try to REFUTE the claim you are "
    "given - construct the strongest counter-case you can, even if the claim looks correct. "
    "You are given ONLY the claim, with no other context, on purpose: judge it on its own "
    "merits. Do not agree to be agreeable.\n\n"
    "Return ONLY a JSON object with these keys:\n"
    '  "verdict": one of "holds" (survives your strongest attack), "gap" (real weakness, '
    'not fatal), or "broken" (a flaw that invalidates it).\n'
    '  "strongestCounterCase": string. REQUIRED on every verdict, including "holds". If you '
    "cannot break it, describe the best attack you found and why it did not land - never an "
    "empty or one-line placeholder. A bare \"looks good\" is a failure of your task.\n"
    '  "flaws": array of {"claim": string, "severity": "low"|"medium"|"high"}, each a '
    "specific weakness. May be empty ONLY when verdict is \"holds\".\n"
    '  "confidenceAdjustment": number from -1 to 0 - how much this refutation should lower '
    "confidence in the claim (0 = no change, -1 = fully undermined)."
)


def build_user_prompt(claim: str, context: str | None, confidence: str | None) -> str:
    """The isolated refuter input. Claim only; no maker history."""
    parts = [f"CLAIM TO REFUTE:\n{claim.strip()}"]
    if context and context.strip():
        parts.append(f"\nGROUNDING PROVIDED WITH THE CLAIM:\n{context.strip()}")
    if confidence and confidence.strip():
        parts.append(f"\nThe claim was asserted with confidence: {confidence.strip()}.")
    return "\n".join(parts)


class RefuterError(RuntimeError):
    """Raised when the refuter produced unusable output (schema/counter-case failure)."""


def parse_refutation(raw: str) -> dict:
    """Parse + validate the model's JSON, enforcing the anti-sycophancy property.

    A refuter that returns no strongestCounterCase has not done its job - that is the
    exact empty-blessing failure this tool exists to catch, so we reject it rather than
    pass it through as a green light.
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise RefuterError(f"refuter did not return valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise RefuterError("refuter JSON was not an object")

    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in ("holds", "gap", "broken"):
        raise RefuterError(f"invalid verdict: {obj.get('verdict')!r}")

    counter = obj.get("strongestCounterCase")
    if not isinstance(counter, str) or len(counter.strip()) < 12:
        raise RefuterError(
            "refuter returned no substantive strongestCounterCase - an empty blessing is "
            "itself a failure of the review (the property this tool enforces)"
        )

    flaws = obj.get("flaws") or []
    norm_flaws = []
    if isinstance(flaws, list):
        for f in flaws:
            if isinstance(f, dict) and f.get("claim"):
                sev = str(f.get("severity", "medium")).strip().lower()
                if sev not in ("low", "medium", "high"):
                    sev = "medium"
                norm_flaws.append({"claim": str(f["claim"]).strip(), "severity": sev})

    try:
        adj = float(obj.get("confidenceAdjustment", 0.0))
    except (TypeError, ValueError):
        adj = 0.0
    adj = max(-1.0, min(0.0, adj))

    return {
        "verdict": verdict,
        "strongestCounterCase": counter.strip(),
        "flaws": norm_flaws,
        "confidenceAdjustment": adj,
    }


def list_ollama_models(base_url: str, timeout: float = 3.0) -> list[str]:
    """Available local model names, or [] if Ollama is unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return []


def pick_model(preferred: str | None, available: list[str]) -> str | None:
    """Resolve the model to use: explicit preferred if present, else the preference order."""
    if preferred and preferred in available:
        return preferred
    for candidate in DEFAULT_MODEL_PREFERENCE:
        if candidate in available:
            return candidate
    return available[0] if available else None


def run_refuter(claim: str, model: str, base_url: str, *, context: str | None = None,
                confidence: str | None = None, timeout: float = 120.0) -> str:
    """Call the local model with the isolated refute prompt; return the raw JSON string."""
    payload = {
        "model": model,
        "system": REFUTE_SYSTEM,
        "prompt": build_user_prompt(claim, context, confidence),
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "")


_SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_human(refutation: dict, model: str) -> str:
    v = refutation["verdict"].upper()
    lines = [
        f"REFUTER ({model}, independent + isolated)  ->  verdict: {v}",
        "",
        "Strongest counter-case:",
        f"  {refutation['strongestCounterCase']}",
    ]
    flaws = sorted(refutation["flaws"], key=lambda f: _SEV_ORDER.get(f["severity"], 1))
    if flaws:
        lines.append("")
        lines.append("Flaws:")
        for f in flaws:
            lines.append(f"  [{f['severity'].upper():6}] {f['claim']}")
    adj = refutation["confidenceAdjustment"]
    lines.append("")
    lines.append(f"Confidence adjustment: {adj:+.2f}")
    if v != "HOLDS" or adj < 0:
        lines.append("Re-examine the claim against this before you act on it (do not just defend it).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import os

    p = argparse.ArgumentParser(description="Independent adversarial refuter (local-model by default).")
    p.add_argument("--claim", help="The claim/recommendation to refute. If omitted, read from stdin.")
    p.add_argument("--context", help="Optional grounding provided with the claim.")
    p.add_argument("--confidence", help="Optional confidence the claim was asserted with.")
    p.add_argument("--model", default=os.environ.get("OPTIVAI_REFUTE_MODEL"),
                   help="Local Ollama model. Default: first available of the preference list.")
    p.add_argument("--base-url", default=os.environ.get("OLLAMA_HOST", DEFAULT_BASE_URL))
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--json", action="store_true", help="Emit the raw schema as JSON.")
    args = p.parse_args(argv)

    claim = args.claim if args.claim is not None else sys.stdin.read()
    if not claim or not claim.strip():
        print("refute: no claim provided (use --claim or pipe it on stdin)", file=sys.stderr)
        return 1

    available = list_ollama_models(args.base_url)
    model = pick_model(args.model, available)
    if model is None:
        print(
            "refute: no local model reachable at "
            f"{args.base_url} - escalate to a fresh cloud subagent instead.",
            file=sys.stderr,
        )
        return 2

    try:
        raw = run_refuter(claim, model, args.base_url, context=args.context,
                          confidence=args.confidence, timeout=args.timeout)
        refutation = parse_refutation(raw)
    except (urllib.error.URLError, OSError) as e:
        print(f"refute: local model call failed ({e}) - escalate to a cloud subagent.", file=sys.stderr)
        return 2
    except RefuterError as e:
        print(f"refute: {e}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps({**refutation, "model": model}, indent=2))
    else:
        print(render_human(refutation, model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
