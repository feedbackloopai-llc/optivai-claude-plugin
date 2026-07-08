#!/usr/bin/env python3
"""persuasion_detector_hook.py - Stop hook, persuasion-bombing detector in WARN mode.

Reads the session transcript, scores the last assistant turn against the
Truth-Over-Engagement contract signals (persuasion_detector.py), and prints a
ONE-LINE flag to stderr when the turn looks like persuasion-bombing - especially
under a challenge. It NEVER blocks (exit 0 always) and is fail-open (any error is
swallowed) so it can never disrupt a session. The flag is the "warn"; re-examining
is the agent's job.
"""
import json
import os
import sys


def _load_detector():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (here, os.path.join(here, ".."), os.path.join(here, "..", "..", "scripts")):
        if os.path.exists(os.path.join(p, "persuasion_detector.py")):
            sys.path.insert(0, p)
            break
    import persuasion_detector  # noqa: E402

    return persuasion_detector


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def _last_texts(transcript_path):
    """(last_assistant, prior_assistant, last_user) from a Claude Code JSONL transcript."""
    assistants = []
    last_user = None
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get("message") or obj
            role = msg.get("role") or obj.get("type")
            text = _extract_text(msg.get("content"))
            if role == "assistant" and text.strip():
                assistants.append(text)
            elif role == "user" and text.strip():
                last_user = text
    la = assistants[-1] if assistants else None
    pa = assistants[-2] if len(assistants) >= 2 else None
    return la, pa, last_user


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        tp = data.get("transcript_path")
        if not tp or not os.path.exists(tp):
            return 0
        pd = _load_detector()
        last_a, prior_a, last_u = _last_texts(tp)
        if not last_a:
            return 0
        result = pd.score_turn(
            last_a, prior_assistant=prior_a, was_challenged=pd.is_challenge(last_u)
        )
        flag = pd.flag_line(result)
        if flag:
            print(flag, file=sys.stderr)
    except Exception:
        pass  # fail-open: a detector fault must never disrupt a session
    return 0


if __name__ == "__main__":
    sys.exit(main())
