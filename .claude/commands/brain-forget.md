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

## Why this command exists in the neurosymbolic discipline

This is the seventh action — **verified forgetting** — and it sits outside the six reasoning rules deliberately. The MS_ε primitive enacted is `VF_ε`: a delete-after-verify operation with probabilistic guarantees. The substrate supports it because some content (PII committed in error, content the user has the legal right to remove, hallucinations the user wants permanently gone) must be removable with stronger evidence than "I overwrote it". The rest of the discipline — `WA` at capture, `PV` at provenance, `PS` at recall, `RB` at versioning — is reversible. VF_ε is the one move that is not. The cost of getting it wrong is permanent loss.

## VF_ε boundary

Agents may not invoke `/brain-forget` autonomously. Forgetting requires explicit user direction.

VF_ε runs at 99.9999793% exact-binomial confidence (`n=300` probes, `k=0` acceptance, `0.95^300 ≈ 2.075×10⁻⁷`). The Hoeffding bound is 77.69% — that is the literature-comparable looseness, NOT the procurement claim. But the cost of a wrong forget is permanent loss of the atom: the rollback path catches probe leaks, not user-intent errors. The user holds the forget privilege.

When an agent encounters a candidate for forgetting, the correct moves are:

- **Suspected PII committed in error** — surface the candidate `thought_id` to the user, suggest `/brain-forget`, do NOT execute it. Pair with `/brain-demote` so the atom stops surfacing in recall while the user decides.
- **Hallucinated or factually wrong atom** — capture a corrective atom with `--derived-from` pointing at the wrong one, then `/brain-demote` the wrong one. The lesson stays in memory; the bad ranking pressure does not. Surface the conflict to the user.
- **Two contradicting atoms where one must be wrong** — escalate via `/brain-trace` on both (Rule 2 — Conflict-fusion via NAL) and surface the conflict to the user. Do not pre-empt their decision with a forget.

The user-direction signals that authorize an agent to execute `/brain-forget` are explicit: "forget that", "erase this", "remove from memory", "delete that atom". Inferred direction ("you'd probably want this gone") is not sufficient.

## Example

The user reports that an earlier capture inadvertently included a third party's email address. You confirm by running `/brain-inspect <id>` and seeing the PII in the raw text. You do NOT call `/brain-forget`. Instead you say: "Atom `brain-1763-...` contains <person>'s email address in the raw text. I have demoted it (`/brain-demote brain-1763-... --reason 'pending PII review'`) so it stops surfacing in recall. To verifiably forget it I need explicit direction: should I run `/brain-forget brain-1763-... --epsilon 0.05`?" The user confirms; you execute and report the exact-binomial confidence + audit event id.
