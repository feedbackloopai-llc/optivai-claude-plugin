Promote or demote a memory's Hebbian weight: $ARGUMENTS

```bash
# Promote (raise retrieval priority)
python3 ~/.claude/hooks/open_brain.py --promote "$ARGUMENTS" --weight 1.0 --reason "useful" --json

# Demote (lower retrieval priority)
python3 ~/.claude/hooks/open_brain.py --demote "$ARGUMENTS" --weight 1.0 --reason "stale" --json
```

This is the **Hebbian promotion** primitive — agent-controlled metacognition over the memory store. Each promote/demote inserts a row in `memory_promotions` with `version_id` + `created_by_principal`, and the effective weight on retrieval is the sum of (weight × decay) over all promotion rows for that thought.

## Time-decay formula

```
effective_weight = Σ_i (raw_weight_i × (1 + days_since_i)^(−0.7))
```

A promotion with `--weight 1.0` applied today contributes 1.0. After 30 days it contributes `(1 + 30)^(−0.7) ≈ 0.094`. After 365 days `≈ 0.014`. This is intentional — fresh signal dominates, but stale promotions do not vanish entirely.

Multiple promotions on the same thought stack (additively, each with its own decay clock). Demotions stack the same way with a negative sign.

## What the agent should do with the JSON output

- Confirm which thought was acted on and the new effective weight (the response includes the post-promotion summed weight).
- If the user is iterating on retrieval relevance ("this answer was unhelpful"), suggest demoting the underlying thought.
- If the user says "always surface this when I ask about X", promote with a higher `--weight` (e.g. 3.0) and a `--reason` describing the anchor topic.

## When promote vs. demote

| Use `--promote` when | Use `--demote` when |
|---|---|
| A memory was load-bearing for a good answer | A memory surfaced but was misleading or off-topic |
| The user explicitly says "this is important" | A memory is technically correct but stale (better version exists) |
| You want a decision rationale to anchor future searches | A working_memory thought has been superseded by a settled decision |

## Example output

```json
{
  "thought_id": "brain-1763-abc",
  "action": "promote",
  "raw_weight": 1.0,
  "effective_weight_after": 2.094,
  "promotion_count": 3,
  "created_by_principal": "claude-code",
  "reason": "useful"
}
```

## When NOT to use this command

- To delete a thought entirely — use `--forget` (VF_ε) for verified deletion, or `--rollback` for plain reversion.
- To mark a memory "do not surface" — demoting is soft; the thought remains retrievable. If you need a hard exclusion, capture a corrective thought and demote the original.

## Why this command exists in the neurosymbolic discipline

This is the agent's instrument for **Rule 4 — Promote-on-validation** and its inverse. The action sits downstream of `PV` (Provenance Visibility): the Hebbian weight is the agent's signal back to the substrate that an atom's truth value should weigh higher (or lower) in future recall ranking. Promotion is time-decayed by design — boosting an old atom that re-validated under fresh evidence is correct, because without the boost, decay would let it fade. The boost is also gated by the within-kind floor, so a heavily-promoted-but-irrelevant atom cannot outrank a lower-promoted-but-relevant one; the Hebbian signal cannot override semantic similarity, only break ties within it.

## When to invoke

- Use `/brain-promote` AFTER a recalled atom unexpectedly mattered — prevented a regression, surfaced a non-obvious constraint, turned out to be load-bearing, earned re-validation through fresh evidence. That's the Hebbian signal that protects it from time decay (Rule 4).
- Promote when the user explicitly reinforces — "yes this is important", "remember this matters", "always surface this when I ask about X". Use higher `--weight` (2.0–3.0) for anchor-topic promotions.
- Use `/brain-demote` (the same command with `--demote`) when a recall was misleading but the atom should not be forgotten outright — the lesson is preserved, the ranking pressure removes it from the top of future results.
- Use `/brain-demote` when the user says "stop bringing this up" but the data still has audit value. Demotion is soft; the atom remains retrievable, the prov-chain is intact.
- Use `/brain-demote` when you suspect a hallucinated or PII-leaked atom; the correct move is demote + surface to the user, NOT `/brain-forget`. Forgetting is reserved for the user (see `/brain-forget` for the VF_ε boundary).

## How to use the result

- The returned `effective_weight_after` is the post-promotion summed weight (sum over all promotion rows after time-decay). A jump from 0.0 to 1.0 confirms a fresh promotion landed; a jump from 2.094 to 3.094 confirms an additive boost on a previously promoted atom.
- If you promoted because the user reinforced, capture the reinforcement reason in `--reason` — this is the audit trail input that explains why the boost exists when someone runs `/brain-replay` later.
- If a promotion and a demotion both apply, pick one; do not stack a 1.0 promote and a 1.0 demote on the same atom. The net effect is zero and the audit log gets noisy.
- After promoting, do not re-promote the same atom on every session — the Hebbian signal is meant to be triggered by re-validation events, not by repeated reads.

## Example

You designed an HTTP retry policy this morning, and `/brain-search "retry"` had surfaced a 2024 pattern atom about jitter preventing thundering-herd synchronization. Without the jitter recommendation your first draft would have shipped the bug; the recall was load-bearing. You run `/brain-promote brain-1763-jitter --weight 1.5 --reason "prevented thundering-herd regression in v0.30 retry policy design"`. The effective weight jumps from 1.2 to 2.7. Next month another agent designing a webhook retry recalls the jitter pattern at rank 1 instead of rank 4 — the boost compounded the prior agent's discipline.
