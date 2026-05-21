# MS_ε Primer — What's new in Open Brain v0.2.0-neurosymbolic

**Audience:** partners, procurement leads, and engineers evaluating Open Brain for production use.
**Read time:** ~5 minutes.
**One-line summary:** v0.2.0 implements the five MS_ε mnemonic-sovereignty primitives (Lin/Li/Chen 2026 §12.1) at the harness level, with a procurement-grade 99.9999793% verified-forgetting guarantee.

---

## The five primitives

Lin/Li/Chen (arXiv [2604.16548](https://arxiv.org/abs/2604.16548), §12.1) define **ε-Mnemonic Sovereignty** over a memory store `Mt` as:

```
MS_ε(Mt) := WA ∧ PV ∧ PS ∧ RB ∧ VF_ε
```

| Primitive | One-line definition | CLI surface |
|---|---|---|
| **WA** — Write Authorization | No writes without an authenticated principal; schema validation + PII redaction (RE2) at the gate. | `--capture` |
| **PV** — Provenance Visibility | Every atom carries a W3C PROV-DM stamp: `agent`, `activity`, `wasGeneratedBy`, `wasDerivedFrom?`, `sourceUri?`. Anonymous writes are rejected at the type level. | `--capture --prov-agent --prov-activity --derived-from`; `--trace` to walk chains |
| **PS** — Principal Scoping | Reads and writes are tenant-scoped; the type system makes cross-tenant reads structurally impossible. | enforced on every CLI verb (no explicit flag) |
| **RB** — Rollbackability | Every mutation creates a new revision in `atom_versions`; rollback creates NEW history, it never rewrites — so revision 1 stays queryable forever. | `--snapshot`, `--versions`, `--rollback`, `--diff`, `--inspect` |
| **VF_ε** — Verified Forgetting | Delete-after-verify: pre-delete snapshot → delete → n=300 probes → accept iff k=0. Dual-bound audit (Hoeffding + exact binomial). | `--forget` |

---

## The dependency pre-order — and why it matters

```
VF_ε  ⪯  RB  ⪯  PV  ⪯  WA
```

Read this right-to-left: **a system without write authorization cannot have meaningful provenance** (anonymous writes have no agent to attribute). **A system without provenance cannot rollback** (you can't restore what you can't identify). **A system without rollback cannot verify forgetting** (you have no pre-delete snapshot to seed probes against). The order is not aesthetic — it is the engineering build order. Any harness that ships "verified forgetting" without first shipping rollback is selling a façade.

This is why the implementation wave order was: PROV-DM first, RB (atom_versions table) second, VF_ε third.

---

## The 99.9999793% procurement headline

The exact binomial confidence at `n=300, k=0, ε=0.05`:

```bash
python3 -c "print((1 - 0.05) ** 300)"
# 2.0747734557863635e-07
python3 -c "print(1 - (1 - 0.05) ** 300)"
# 0.9999997925226544
```

`0.9999997925…` rounded to 5 significant figures is **99.9999793%**. This is the number that appears in procurement materials. The Hoeffding one-sided bound at the same parameters is `exp(−2·300·0.05²) = 0.2231`, which gives only 77.69% confidence — Hoeffding is included in the audit log for academic conservatism, but it is **not** the procurement claim. The audit log emits both as distinct labeled fields (`hoeffdingConfidence`, `exactBinomialConfidence`) so a reviewer can verify which number is being quoted.

---

## The delete-after-verify pattern

The naive ordering — "verify first, then delete" — has a fatal flaw: the verification probes can't query the post-deletion state until the delete happens. So the original VF_ε design tried "delete, verify, rollback if leak". That introduced a data-loss bug: if the probe stage crashed mid-run, the atom was gone but the verification audit was never written, so the operator could not prove the forgetting was sound.

The shipped pattern resolves this:

1. Take a `ProbeSeedSnapshot` BEFORE the delete: full text of the target atom, plus the text of its top-50 semantic neighbours. This snapshot lives entirely in-process, in memory.
2. Delete the atom (versioned — `atom_versions` retains the history).
3. Issue `n=300` probes against the post-delete vector store, distributed 40/30/20/10 across (semantic-neighbor / paraphrase / partial-fragment / embedding-perturb), using the in-memory snapshot to phrase the probes.
4. Accept iff `k=0`. On any leak, `RB` restores the atom and the audit row records `status=rollback` with the leaking probe.

The snapshot is the load-bearing change: it makes the probe phase independent of the post-delete state of the store, so a crash in step 3 still leaves a complete audit row in step 4 (or a clean rollback). No data-loss window exists.

---

## Proof points (test corpus locations)

| Claim | Test file |
|---|---|
| Forget CLI behaves correctly end-to-end | `tests/test_forget_cli.py` |
| VF_ε probe distribution + dual-bound math | `tests/test_vf_probe.py` |
| Hoeffding ≠ exact binomial audit fields | `tests/test_vf_eps_corpus.py` |
| Citation walker + orphan markers | `tests/test_citation_walker.py` |
| Time-travel + rollback-creates-new-history | `tests/test_inspect_memory.py` |
| Hebbian promote/demote time-decay | `tests/test_hebbian.py` |
| Replay log + PII-distinct redaction | `tests/test_replay_log.py` |

---

## Further reading

- Lin/Li/Chen 2026 — *Mnemonic Sovereignty in Language Model Memory Systems* — arXiv [2604.16548](https://arxiv.org/abs/2604.16548). §11.4 explicitly argues that sovereignty primitives belong at the harness layer, not bolted onto each app. Open Brain v0.2.0 IS that harness.
- W3C PROV-DM (REC-prov-dm-20130430) — the provenance ontology Open Brain conforms to.
- `docs/OPEN_BRAIN_PARTNER_SETUP.md` — the install + first-thought walkthrough for partner engineers.
- The five new CLI verbs are documented in `.claude/commands/brain-{forget,trace,inspect,promote,replay}.md`.

When partners ask "what's new in 0.2", point them here.
