Replay the session audit log: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --replay --json
```

This emits the **chronological audit log** of every brain operation in the time window. Each entry is the OTel-correlated event recorded at the time the operation ran — capture, search, promote/demote, forget, inspect, trace. PII in raw thought text is redacted; the structural metadata (agent, activity, session_id, trace_id, event_type, timestamp) is preserved verbatim so the log is procurement-auditable.

## PII-distinct redaction

The log distinguishes two text classes:

- **Structural fields** (agent, activity, session_id, event_type, principal, project) — NEVER redacted. These are required for audit reconstruction.
- **Free-text fields** (raw_text excerpts, query strings, reason strings) — passed through the RE2 PII redactor (same gate that runs at capture time). Emails, phone numbers, credit cards, and configured custom patterns become `[REDACTED:KIND]` tokens.

This is intentional and procurement-required: an auditor reviewing the log must be able to verify WHO did WHAT WHEN against WHICH session, without that audit creating a new PII exposure.

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
