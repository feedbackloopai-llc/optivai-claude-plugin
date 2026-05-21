# Neurosymbolic Memory — What's new in Open Brain v0.2.0

**Audience:** anyone using Open Brain inside Pi or Claude Code who wants to understand the new memory primitives.
**Read time:** ~5 minutes.
**One-line summary:** v0.2.0 extends the brain from "semantic vector recall" to a full neurosymbolic memory substrate with provenance, versioning, agent-controlled promotion, and verified forgetting.

---

## What the brain could do before

v0.1.x: capture a thought → 768-dim embedding via `all-mpnet-base-v2` → pgvector cosine search recovers it by meaning later. That's strong vector recall but it's structurally lossy — there's no way to ask "where did this memory come from?", "what did this thought look like a week ago?", "promote this thought so it surfaces faster", or "forget this and prove it's gone."

## What v0.2.0 adds

Five new primitives that turn the brain from a search index into a substrate your coding agents can reason over:

| Primitive | What it gives the agent | CLI surface |
|---|---|---|
| **Provenance (PV)** — W3C PROV-DM 1.3 fields on every thought | "Where did this memory come from? Who wrote it? What was it derived from?" | `--capture --prov-agent --prov-activity --derived-from`; `--trace` walks the chain |
| **Principal scoping (PS)** — every operation scoped to `user_id` | Cross-user reads are structurally impossible | enforced on every CLI verb |
| **Rollback (RB)** — versioned mutations in `thought_versions` | "Snapshot this state, edit, then revert — but never lose the intermediate history" | `--snapshot`, `--versions`, `--rollback`, `--diff`, `--inspect` |
| **Verified forgetting (VF_ε)** — delete-after-verify with statistical residue check | "Forget this thought AND prove with high confidence that it's gone, not just hidden" | `--forget` |
| **Hebbian promotion** — agent-controlled weight + time-decay | "I keep coming back to this thought — surface it faster in future searches" | `--promote`, `--demote` |

Plus the **replay log** — a PII-redacted chronological audit of every brain operation, useful for debugging "what did the agent retrieve before making decision X."

---

## The formal framework (optional reading)

The five primitives map directly to the **ε-Mnemonic Sovereignty** framework from Lin/Li/Chen 2026 (arXiv [2604.16548](https://arxiv.org/abs/2604.16548), §12.1):

```
MS_ε(Mt) := WA ∧ PV ∧ PS ∧ RB ∧ VF_ε
```

The paper's value (and the reason we used it as the design template) is the **dependency pre-order**:

```
VF_ε  ⪯  RB  ⪯  PV  ⪯  WA
```

Read right-to-left: a system without write authorization can't have meaningful provenance; a system without provenance can't rollback meaningfully (you can't restore what you can't identify); a system without rollback can't verify forgetting (no pre-delete snapshot to probe against). The order is the engineering build order. If you ever see a memory layer claiming "verified forgetting" without rollback, it's a façade.

We built the waves in this order: PROV-DM (PV) → `thought_versions` (RB) → `vf_probe` (VF_ε).

---

## The forget guarantee

`--forget` is the primitive worth understanding in detail because it makes a probabilistic claim.

The math: at n=300 probes / k=0 surfaced / ε=0.05 target:

```bash
python3 -c "print((1 - 0.05) ** 300)"
# 2.075303347768056e-07
python3 -c "print(1 - (1 - 0.05) ** 300)"
# 0.9999997924696653
```

That's about **99.9999793% confidence** that no residue of the forgotten content remains queryable (exact-binomial bound). The Hoeffding one-sided bound at the same parameters gives only ~77.7% — looser but distribution-free. The audit log records both as distinct fields (`hoeffdingConfidence`, `exactBinomialConfidence`) so you can see which number is being quoted by anything reading the audit trail.

You can dial these: `--epsilon 0.10 --n 500` for a different operating point. Default `--epsilon 0.05 --n 300` is the calibrated sweet spot.

---

## The delete-after-verify pattern

The naive ordering — "verify first, then delete" — doesn't work, because the verification probes can't query the post-deletion state until the delete actually happens. The original VF_ε design tried "delete, verify, rollback if leak", which has a data-loss window: if the probe stage crashes mid-run, the thought is gone but no audit row exists.

The shipped pattern resolves this:

1. Take a `ProbeSeedSnapshot` **before** the delete: full text of the target thought plus the text of its top-50 semantic neighbors. This snapshot lives in process memory.
2. Delete the thought (`thought_versions` cascades, but the snapshot persists in memory).
3. Issue `n=300` probes against the post-delete vector store, distributed 40/30/20/10 across (semantic-neighbor / paraphrase / partial-fragment / embedding-perturb), using the in-memory snapshot to phrase the probes.
4. Accept iff `k=0`. On any leak, the row is re-INSERTed from the snapshot (no data lost) and the audit records `status=forget-failed-residue`.

The snapshot is the load-bearing change: it makes the probe phase independent of the post-delete state of the store, so a crash in step 3 still leaves a complete audit row in step 4 (or a clean restore).

---

## What this means for the coding agents

- **Provenance for memory inspection.** When an agent recalls a thought via search, it now has structured access to "what activity produced this, what's it derived from, can I walk back to the origin." `--trace` is the natural surface for this.
- **Versioned memory.** Long-running agent sessions can snapshot state, try an alternate path, and roll back without losing the intermediate work. `--inspect --at-revision N` exposes every historical state.
- **Agent-controlled promotion.** When an agent recognizes "this thought turned out to matter more than its raw similarity score suggested," it can promote. The time-decay (`weight × (1 + days_since)^(−0.7)`) means the boost fades naturally unless reinforced.
- **Verifiable forget.** "Delete and forget" is a real guarantee, not a marketing line. The audit log lets the agent (or the user) verify after the fact.
- **Replay for self-introspection.** An agent can read its own `--replay` log to answer "what did I retrieve in this session?" — useful for debugging unexpected behavior.

---

## Proof points (test files in this repo)

| Claim | Test file |
|---|---|
| Forget CLI behaves correctly end-to-end | `tests/test_forget_cli.py` |
| VF_ε probe distribution + dual-bound math | `tests/test_vf_probe.py` |
| Hoeffding vs exact binomial recorded distinctly | `tests/test_vf_eps_corpus.py` |
| Citation walker + orphan markers | `tests/test_citation_walker.py` |
| Time-travel + rollback-creates-new-history | `tests/test_inspect_memory.py` |
| Hebbian promote/demote time-decay | `tests/test_hebbian.py` |
| Replay log + PII-distinct redaction | `tests/test_replay_log.py` |

---

## Further reading

- Lin/Li/Chen 2026 — *Mnemonic Sovereignty in Language Model Memory Systems* — arXiv [2604.16548](https://arxiv.org/abs/2604.16548). §11.4 argues that sovereignty primitives belong at the harness layer, not bolted onto each app. This is the design template Open Brain v0.2.0 follows.
- W3C PROV-DM (REC-prov-dm-20130430) — the provenance ontology our `prov_agent / prov_activity / was_generated_by / was_derived_from / source_uri` columns conform to.
- `docs/OPEN_BRAIN_PARTNER_SETUP.md` — install + first-thought walkthrough.
- The five new CLI verbs are documented in `.claude/commands/brain-{forget,trace,inspect,promote,replay}.md`.
