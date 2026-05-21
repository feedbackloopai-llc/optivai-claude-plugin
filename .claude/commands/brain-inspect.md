Inspect a memory's state at a point in time: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --inspect "$ARGUMENTS" --json
```

This is the **time-travel** primitive. It exposes the `atom_versions` table — every mutation to a thought creates a new monotonically-numbered revision, and old revisions are never overwritten. **Rollback creates new history**: rolling back to revision 1 inserts a new revision (e.g. revision 7) whose payload matches revision 1. Revision 1 itself stays queryable forever.

## Three modes

| Flag combination | What it returns |
|---|---|
| `--inspect <id>` (no time flag) | The latest revision (equivalent to a normal read). |
| `--inspect <id> --at 2026-05-15T12:00:00Z` | The revision that was authoritative AT that timestamp (i.e. greatest `created_at` ≤ the supplied ISO time). |
| `--inspect <id> --at-revision 3` | The specific revision number, regardless of when it was written. |

`--at` and `--at-revision` are mutually exclusive — passing both is a CLI error.

## What the agent should do with the JSON output

- Lead with the resolved revision number and its `created_at` so the user knows which snapshot they got.
- Show the full text + metadata at that revision.
- If the caller passed `--at`, also surface "the next revision after this one was at <timestamp>" so they understand the validity window.
- If the user asks "what changed between revision X and Y", recommend `--diff <id> --from-revision X --to-revision Y` as the follow-up.

## Example usage

```bash
# Latest state
python3 ~/.claude/hooks/open_brain.py --inspect brain-1763-abc --json

# State on a specific day
python3 ~/.claude/hooks/open_brain.py --inspect brain-1763-abc --at 2026-04-01T00:00:00Z --json

# A specific historical revision
python3 ~/.claude/hooks/open_brain.py --inspect brain-1763-abc --at-revision 1 --json
```

## Example output

```json
{
  "thought_id": "brain-1763-abc",
  "revision": 3,
  "created_at": "2026-05-10T14:23:00Z",
  "raw_text": "...",
  "prov": {"agent": "claude-code", "activity": "update", "wasGeneratedBy": "2026-05-10T14:23:00Z"},
  "next_revision_at": "2026-05-12T09:11:00Z"
}
```

## When NOT to use this command

- To list every version — use `--versions <id>` for that, then `--inspect` the specific revision you want.
- For "show me the diff" — use `--diff <id> --from-revision A --to-revision B` instead; `--inspect` returns full snapshots, not deltas.
