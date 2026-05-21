Verified-forget a memory with MS_ε guarantee: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --forget "$ARGUMENTS" --epsilon 0.05 --n 300 --json
```

This invokes the **VF_ε primitive** (verified forgetting). It is not a `DELETE`. It is a delete-after-verify operation that produces a strong probabilistic guarantee that the forgotten content can no longer be recovered by semantic search.

## What happens (delete-after-verify pattern)

1. A **ProbeSeedSnapshot** is taken: the target memory's full text + its top-50 semantic neighbours' text.
2. The atom is deleted from `brain.thoughts` (versioned: history is preserved in `atom_versions`).
3. `n=300` probes are issued against the post-delete state, distributed 40/30/20/10 across (semantic-neighbor / paraphrase / partial-fragment / embedding-perturb).
4. Accept iff `k=0` probes return the forgotten content. Any leak rolls the deletion back via `RB` and reports failure.

## The dual-bound audit (both numbers are recorded)

| Bound | Formula | Value | Confidence |
|---|---|---|---|
| Hoeffding (one-sided, conservative) | `exp(−2·n·ε²)` at n=300, ε=0.05 | `0.2231` | 77.69% |
| Exact binomial (k=0, tight) | `(1−ε)^n` at n=300, ε=0.05 | `2.075×10⁻⁷` | **99.9999793%** |

The "99.9999793% verified forgetting" headline is the **exact binomial** confidence. The Hoeffding bound is included for distribution-free conservatism; both are emitted as distinct labeled fields in the audit log (`hoeffdingBound`, `hoeffdingConfidence`, `exactBinomialBound`, `exactBinomialConfidence`, `probeQuality`). Quote the exact-binomial number as the strength of the guarantee.

## What the agent should do with the JSON output

- Report which thought_id was forgotten and confirm `k=0` (or surface the failure if k>0).
- Quote the exactBinomialConfidence (5 sig figs).
- If the user wants context on the math, point them at `docs/MS_EPS_PRIMER.md`.
- If a leak occurred (`status: "rollback"`), surface the audit_event_id so the operator can inspect which probe returned the content.

## Example output (success)

```json
{
  "thought_id": "brain-1763...-abc123",
  "status": "verified_forgotten",
  "n": 300,
  "k": 0,
  "epsilon": 0.05,
  "hoeffdingBound": 0.22313,
  "hoeffdingConfidence": 0.77687,
  "exactBinomialBound": 2.075e-7,
  "exactBinomialConfidence": 0.999999792,
  "probeQuality": "good",
  "audit_event_id": "evt-..."
}
```

## When NOT to use this command

- For routine cleanup of stale memories — use a regular update/demote instead; VF_ε is expensive (~5–10 s per call).
- To "undo" a capture immediately after the fact — that's what `--rollback` is for.
