Replay the session audit log: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --replay --json
```

This emits the **chronological audit log** of every brain operation in the time window. Each entry is the OTel-correlated event recorded at the time the operation ran — capture, search, promote/demote, forget, inspect, trace. PII in raw thought text is redacted; the structural metadata (agent, activity, session_id, trace_id, event_type, timestamp) is preserved verbatim so the log is auditable.

## PII-distinct redaction

The log distinguishes two text classes:

- **Structural fields** (agent, activity, session_id, event_type, principal, project) — NEVER redacted. These are required for audit reconstruction.
- **Free-text fields** (raw_text excerpts, query strings, reason strings) — passed through the RE2 PII redactor (same gate that runs at capture time). Emails, phone numbers, credit cards, and configured custom patterns become `[REDACTED:KIND]` tokens.

This is intentional: an auditor reviewing the log must be able to verify WHO did WHAT WHEN against WHICH session, without that audit creating a new PII exposure.

## Filtering flags

| Flag | Effect |
|---|---|
| `--session-id <id>` | Restrict to one session (most common: drill into "what did this Claude Code session do?"). |
| `--from <ISO>` / `--to <ISO>` | Time window. ISO 8601 only; both bounds are inclusive. |
| `--event-type <name>` | One of: `capture`, `search`, `promote`, `demote`, `forget`, `inspect`, `trace`, `rollback`, `update`. Pass once. |
| `--limit <N>` | Cap row count (default applies). |

## What the agent should do with the JSON output

- Lead with the window summary: N events between <from> and <to>, M distinct sessions, K distinct event types.
- Group by session_id when the user is doing a session post-mortem. Group by event_type when the user is auditing a primitive ("show me every --forget call this week").
- If the user spots a row of interest, follow up with `--inspect <thought_id> --at-revision <N>` to see the actual state at that audit moment.
- Surface `trace_id` values so the user can correlate across systems (Django side, Pi side, builder side share trace IDs).

## Example usage

```bash
# Everything this Claude Code session has done
python3 ~/.claude/hooks/open_brain.py --replay --session-id "$CLAUDE_CODE_SESSION_ID" --json

# Every --forget call in the last 7 days (compliance audit)
python3 ~/.claude/hooks/open_brain.py --replay --from 2026-05-14T00:00:00Z --event-type forget --json

# A specific day's activity
python3 ~/.claude/hooks/open_brain.py --replay --from 2026-05-19T00:00:00Z --to 2026-05-19T23:59:59Z --json
```

## When NOT to use this command

- For "what did I think about" — that's `/brain-recent` or `/brain-search`. Replay is the audit log, not the memory itself.
- For long-range trend analysis — replay returns event rows, not aggregates. Use `/brain-stats` for distribution.

## Why this command exists in the neurosymbolic discipline

This is the **own-actions flavor of Rule 6 — Provenance-traversal**. Where `/brain-trace` walks the derivation chain of one atom and `/brain-inspect` walks the version history of one atom, `/brain-replay` walks the chronological sequence of brain operations a session (or principal, or time window) performed. The MS_ε primitive enacted is `PV` (Provenance Visibility) sliced along the audit-log axis: every capture, search, promote, demote, forget, inspect, trace, rollback, and update emits an OTel-correlated event row at the time it ran. Replay is how an agent reconstructs "what did I retrieve before making decision X?" rather than guessing from current state.

## When to invoke

- Post-decision diagnostics — "what did the agent retrieve before making decision X?" Pull the replay window around the decision and inspect the searches and atoms that surfaced (Rule 6 along the audit axis).
- Self-review at the end of a session — "show me everything I captured and promoted today" to verify Rule 3 (capture-with-alternatives) and Rule 4 (promote-on-validation) actually fired.
- Compliance audit — chronological event log scoped to user_id and time window. The PII-distinct redaction makes this safe to share with an auditor.
- When the user asks "did you forget something?" — filter by `--event-type forget` to surface every VF_ε call in the window. The VF_ε boundary forbids autonomous forgets; replay is how that boundary is verified after the fact.
- Cross-system correlation — replay returns `trace_id` values shared with Django, Pi, and builder side, so an auditor can correlate a single user action across the full stack.

## How to use the result

- Group by `session_id` for a session post-mortem; group by `event_type` for a primitive-level audit ("every promote this week"); group by `thought_id` to reconstruct the full audit trail on one atom.
- Replay is read-only: no atom is being modified, no Hebbian or VF_ε follow-up is implied. Inspection follow-ups (`/brain-inspect <id> --at-revision <N>`) are the natural next step when a row surfaces a specific atom of interest.
- The PII redaction is one-way; the raw text behind a `[REDACTED:EMAIL]` token is not recoverable from the replay log. To see the original captured text, use `/brain-inspect <id>` against the principal who owns the atom; redaction at capture time is decoupled from the replay redaction.
- If replay surfaces a brain operation that contradicts session intent (e.g., a forget that was not user-directed), surface the row immediately rather than continuing. Replay is the boundary-violation detector for the VF_ε rule.

## Example

End of a long session, the user asks "did we capture the decisions we made today?" You run `/brain-replay --session-id "$CLAUDE_CODE_SESSION_ID" --event-type capture --json`. The output shows 4 captures: the vector-store choice, the retry-policy decision, a person_note about a stakeholder, and a pattern about parallel-implementer git races. Replay also shows 2 promotes (the jitter pattern and the worktree-isolation pattern, both validated during the session), 0 forgets (the VF_ε boundary held), and 11 searches. You summarize the audit trail and confirm Rule 3 fired on every consequential decision; the worktree-isolation pattern's promotion confirms Rule 4 fired on a re-validated recall.
