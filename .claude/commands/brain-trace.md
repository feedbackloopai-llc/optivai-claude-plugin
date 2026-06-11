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
- Each non-orphan node also carries `stv: {f, c}` — the belief truth value at that point in the derivation. A chain produced by `/brain-revise` will show increasing `c` values toward the leaf, reflecting evidence accumulation. A chain with `c` dropping toward the root may indicate the belief was strengthened by more recent evidence (closer to the leaf).
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

## Why this command exists in the neurosymbolic discipline

This is the **lineage-axis flavor of Rule 6 — Provenance-traversal** and the inspection half of **Rule 2 — Conflict-fusion via NAL**. The MS_ε primitive enacted is `PV` (Provenance Visibility): the whole point of stamping PROV-DM at write time (the `WA` gate enforces it) is to make this traversal cheap at read time. Recall returns what looks relevant; traversal verifies it is trustworthy. The orphan-marker discipline means the chain stays auditable even after a parent atom is forgotten under VF_ε — silent truncation would mask the deletion.

## When to invoke

- Before acting on a consequential recalled fact — one that would shape an architectural choice, justify a destructive action, or be cited to the user as authoritative. If the chain ends at a low-confidence source or an older-than-90-day single-source belief, down-weight the atom or seek corroboration (Rule 6).
- When two recalled atoms contradict — trace both before deciding which to trust. The contradiction may resolve naturally if one chain's root atom has higher confidence or more recent PROV-DM stamps (Rule 2 — Conflict-fusion via NAL, inspection step).
- When the user asks "where did this come from?", "what's the source of this memory?", or "how was this derived?" — this is the canonical answer surface.
- During audit/debug — "show me how this thought was derived" surfaces the agent → activity → wasGeneratedBy lineage stamp-by-stamp.

## How to use the result

- Pair with `/brain-inspect <id> --at <ISO>` when the inspection question is "what did the parent atom say at the moment of derivation, not now" — trace gives the link, inspect gives the state at that timestamp.
- Pair with `/brain-timeline "<topic>"` when the question is "how did the topic evolve" rather than "how was this single atom derived" — different axes of Rule 6.
- Pair with `/brain-replay` when the question is "what did the agent retrieve before deriving this" — replay returns the search/recall log around the derivation timestamp.
- After resolving a Rule 2 conflict, capture the fused belief via `/brain-capture --derived-from <both premise ids>` so the new atom's own chain remains auditable.

## Example

You recall a `decision` atom from six weeks ago saying "use IVFFlat for the embedding index". Before acting, you `/brain-trace` it. The chain shows: this decision derived from a benchmark atom (your agent, two months ago), which itself derived from a pgvector tuning guide note (low-confidence external `sourceUri`, three months ago). The chain has one orphan marker — an intermediate working_memory atom the user forgot last month. You note the chain's weakest link is the external tuning guide and that the orphan marker may have contained a counter-argument. Instead of acting on the IVFFlat decision blindly, you surface the chain to the user and ask whether the orphan was the basis of a later reversal.
