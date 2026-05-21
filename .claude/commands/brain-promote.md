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

- To delete a thought entirely — use `--forget` (VF_ε) for procurement-grade deletion, or `--rollback` for plain reversion.
- To mark a memory "do not surface" — demoting is soft; the thought remains retrievable. If you need a hard exclusion, capture a corrective thought and demote the original.
