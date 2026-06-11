Fuse two memories about the same proposition via NAL revision: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --revise ID_A ID_B --json

# With an explicit text override for the derived atom:
python3 ~/.claude/hooks/open_brain.py --revise ID_A ID_B --text "fused claim here" --json
```

`ID_A` and `ID_B` are `thought_id` values from prior captures or search results. Both must belong to the caller's principal scope (PS-scoped).

## What NAL revision does

Revision pools evidence from two independent observations of the **same proposition** using the Non-Axiomatic Logic evidential-horizon formula (Wang 1995 §5.1, k=1):

```
w_i  = c_i / (1 - c_i)            # evidence weight (odds-ratio)
f    = (w1×f1 + w2×f2) / (w1+w2)  # weighted average frequency
c    = (w1+w2) / (w1+w2+1)        # revised confidence
```

The revised confidence `c` is **strictly higher** than either premise — this is the evidence-accumulation property. A high-confidence belief dominates a low-confidence contradicting one (the weight ratio is the confidence odds, not 50/50).

The derived atom is created via the normal `--capture` path, so PROV-DM stamping, PII redaction, embedding, and metadata extraction all apply. After capture:

- `derives_from` links are added from the derived atom to **both** premises.
- `was_derived_from` on the `brain.thoughts` row points to the higher-confidence premise (single-parent schema constraint — the second link lives in `atom_links`).
- If a `contradicts` link already exists between A and B (either direction), `resolves` links are also added from the derived atom to both premises.

The returned `prov_activity` field will read `nal_revision`.

## Why this command exists in the neurosymbolic discipline

This is the agent's instrument for **Rule 2 — Conflict-fusion via NAL**. When `/brain-search` or `/brain-trace` surfaces two atoms that partially contradict — one recorded under lower confidence, one under higher, or both recorded independently — the correct move is NOT to pick one and discard the other. The older atom retains its evidentiary weight; the newer atom adjusts the confidence. Running `--revise` creates an auditable derived belief whose `derives_from` links trace back to both premises, so future agents can walk the chain via `/brain-trace` and see the resolution record.

The MS_ε primitive enacted is `WA` (Write Authorization) at capture time + `PV` (Provenance Visibility) via the `derives_from` chain. The fused atom's `c` value is provably higher than either premise — the math is the audit.

## Scope note

This is NAL-**lite**: revision and evidence propagation (via `verifies`/`refutes` links) only. It is not a general inference engine and does not derive new propositions from existing ones. The full NAL atomspace is in `optivai-builder`. Do not over-claim.

## When to invoke

- Two recalled atoms bearing on the same proposition show conflicting confidence levels — the lower one recorded early under uncertainty, the higher one after more evidence. Revise them to produce a single fused belief (Rule 2).
- You `/brain-trace` two atoms and their provenance chains don't contradict — they are genuinely independent observations of the same claim. Revision strengthens confidence with no belief conflict.
- Two atoms are linked with `contradicts` and you have sufficient context to form a derived position. After `--revise`, the `resolves` links will be automatically added.
- Before citing a recalled fact authoritatively — if that fact has a lower-confidence sibling atom, revising them first gives you a higher-confidence derived fact to cite (Rule 6 preparation).

## When NOT to invoke

- The two atoms are about **different** propositions — revision assumes both atoms measure the same underlying truth. Revising unrelated atoms produces a meaningless composite.
- You want to downgrade a misleading atom — use `/brain-demote` instead.
- You want to update the text of an existing atom — use `--capture` with `--derived-from`.
- You want to derive a new proposition from premises (deduction/abduction) — that requires the full NAL engine in `optivai-builder`, not this lite surface.

## How to use the result

- The returned JSON includes `thought_id` (the new derived atom), `stv` `{f, c}` (the revised truth value), `derives_from` (the two premise IDs), and `contradicts_resolved` (boolean — whether existing `contradicts` links were resolved).
- Confirm `stv.c` is **higher** than both input confidences. If not, inspect the premise `stv` values — it is possible if both premises had high confidence pointing in opposite directions (high-weight conflict compresses the resulting confidence toward the midpoint).
- After creating the derived atom, run `/brain-promote` on it if it is load-bearing for the current decision (Rule 4).
- Run `/brain-trace <derived_id>` to confirm the chain walks back through both premises.
- Demote the lower-confidence premise afterward if it should rank below the derived fusion in future recalls: `/brain-demote <lower_premise_id> --reason "superseded by nal_revision <derived_id>"`.

## Example

You are about to document the retry policy for a new service. `/brain-search "exponential backoff retry"` returns two atoms:

- `brain-1100-abc` (3 months old, `stv f=1.0 c=0.50`): "use 2× backoff up to 30s"
- `brain-1420-def` (2 weeks old, `stv f=1.0 c=0.80`): "use 2× backoff up to 60s, not 30s — measured timeout under load"

Both measure the same proposition (max cap for exponential backoff); `brain-1420-def` supersedes with higher confidence. You run:

```bash
python3 ~/.claude/hooks/open_brain.py \
  --revise brain-1100-abc brain-1420-def \
  --text "Exponential backoff, 2× multiplier, cap at 60s. 30s cap measured too low under load (2026-05-28 benchmark)." \
  --json
```

Result: `stv f=1.0 c=0.87` — higher than either premise (evidence accumulation). The derived atom's `derives_from` links both premises; `/brain-trace` on the new ID shows the resolution chain. You `/brain-demote brain-1100-abc --reason "superseded by nal_revision"` so it no longer surfaces above the fused version.
