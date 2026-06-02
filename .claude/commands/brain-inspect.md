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

## Why this command exists in the neurosymbolic discipline

This is the **time-travel flavor of Rule 6 — Provenance-traversal**. Where `/brain-trace` walks the `wasDerivedFrom` chain (lineage axis) and `/brain-timeline` walks chronological topic evolution (time axis across atoms), `/brain-inspect` walks the version history of a single atom (time axis within one atom). The MS_ε primitive enacted is `RB` (Rollbackability): every mutation creates a monotonic revision; nothing is overwritten in place. This is the substrate under rollback, under verified forgetting, and under any audit question of the form "what did this atom say last week?"

## When to invoke

- Before acting on a recalled atom whose payload may have shifted since the decision it justified — pull the historical revision and check whether the current latest revision still says the same thing.
- After a `/brain-trace` walk surfaces a wasDerivedFrom that points at a parent atom — inspect the parent at the timestamp of derivation, not the parent's current state. The lineage is "what the child was derived from at the time", not "what the parent says now" (Rule 6 — provenance verification).
- For post-rollback diagnostics — confirm the live row matches the rolled-back state by inspecting the latest revision and the rolled-back revision side by side.
- When the user asks "what did I say about X last week?" or "show me the original version" — answer by timestamp or by revision number.
- When verifying a capture succeeded — `--inspect <new_id>` immediately after `/brain-capture` confirms the WA gate passed and the atom committed.

## How to use the result

- Returns `None` if no version exists at the requested time or revision — that is not an error; it means the atom did not yet exist (for `--at <past>`) or the revision number is out of range. Surface the absence to the user rather than treating it as a failure.
- Pair with `/brain-trace <id>` when the inspection question is "where did this state come from" — inspect gives the state, trace gives the derivation chain.
- If a historical revision contradicts the latest revision and the current task depends on the older state, escalate to Rule 2 (Conflict-fusion via NAL) by capturing the resolution with `--derived-from` referencing both revisions.
- Inspection does not mutate the atom; no Hebbian or VF_ε follow-up is implied. Inspection is read-only.

## Example

You recall a decision atom `brain-1763-vector-store` that says "use pgvector". `/brain-trace` shows a child atom from three weeks ago citing this parent. But the latest revision of `brain-1763-vector-store` is revision 4, and the child was derived against revision 2. You run `/brain-inspect brain-1763-vector-store --at-revision 2` and see revision 2 said "use pgvector with HNSW index"; revision 4 says "use pgvector with IVFFlat index after the v0.31 scale review". The child's reasoning was anchored to the HNSW choice, which has since been superseded. You surface the version drift to the user before acting on the child's recommendation.
