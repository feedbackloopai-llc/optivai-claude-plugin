Walk the provenance chain of a memory: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --trace "$ARGUMENTS" --max-depth 50 --json
```

This invokes the **citation walker** — it walks the `was_derived_from` chain (PROV-DM `wasDerivedFrom`) from a target thought back to its source atoms. Each node in the chain includes its PROV-DM stamp (`agent`, `activity`, `wasGeneratedBy`, `wasDerivedFrom`, optional `sourceUri`).

## How the chain is walked

- Starting from `<thought_id>`, the walker follows the `was_derived_from` pointer on each node.
- Walk terminates on three conditions: (a) a node with no `was_derived_from` (root), (b) `--max-depth` reached, or (c) an **orphan marker** — a `was_derived_from` referencing a thought that has been forgotten (VF_ε) or is outside the caller's tenant scope.
- Orphan nodes are emitted with `{"orphan": true, "reason": "vf_forgotten" | "out_of_scope"}` so the caller can render them as placeholder citations rather than silently truncating the chain.
- Default `--max-depth` is 50. Raise it for deep research chains; lower it (e.g. `--max-depth 5`) for quick provenance previews.

## What the agent should do with the JSON output

- Present the chain bottom-up (root → target) so the reader follows derivation forward in time.
- For each node, show: short_id, agent, activity, wasGeneratedBy timestamp, one-line summary.
- Highlight orphan nodes — they reveal where a parent was forgotten via VF_ε, making the chain auditable in both directions.
- If `truncated: true` (max-depth hit), suggest re-running with a higher `--max-depth`.

## Example output

```json
{
  "thought_id": "brain-...-deadbeef",
  "depth": 4,
  "truncated": false,
  "chain": [
    {"id": "brain-...-aaaa", "agent": "claude-code", "activity": "capture", "wasGeneratedBy": "2026-05-12T..."},
    {"id": "brain-...-bbbb", "agent": "ralph-loop", "activity": "synthesize", "wasGeneratedBy": "2026-05-15T..."},
    {"orphan": true, "id": "brain-...-cccc", "reason": "vf_forgotten"},
    {"id": "brain-...-deadbeef", "agent": "claude-code", "activity": "summary", "wasGeneratedBy": "2026-05-20T..."}
  ]
}
```

## When NOT to use this command

- For "what other thoughts mention X" — that's a semantic search (`/brain-search`), not a provenance walk.
- For pure timeline-of-topic rendering — use `/brain-timeline` instead; trace is for derivation lineage, not topical evolution.
