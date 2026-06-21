# Mayor Orchestration Layer — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Per-bead: superpowers:test-driven-development. Parallel waves: superpowers:dispatching-parallel-agents + superpowers:using-git-worktrees.

> **STATUS: DRAFT — pending Chris's validation of (a) v1 scope and (b) parity-sequencing (see "Open Forks").**

**Goal:** Add the multi-agent **orchestration superstructure** (a Mayor coordinator that dispatches a bounded set of role-typed workers over a beads molecule, tracks capacity, recovers crashed workers, and serializes merges) to both dev harnesses — built on our existing substrate (beads + Brain + the loop runner), taking Gas Town's MIT design as the blueprint, adding **no new storage** and **no Dolt**.

**Architecture:** The Mayor is the **single writer** to task state: N workers run concurrently but only *return results*; the Mayor commits status/assignment changes to beads. This is exactly the design Gas Town converged on after abandoning branch-per-worker, so there is **no merge engine** to build. Coordination state lives in the two stores we already run — **beads** (the live task graph) and the **Brain** (the versioned ledger / provenance / Hebbian promotion) — extending `loop_runner.py` (Claude) and `loop-runner.ts` (Pi) into the coordinator. The one place we go deliberately *better* than Gas Town: **OS-level worker isolation is the default, not opt-in.**

**Tech Stack:** Python 3.12 (`optivai-claude-plugin`), TypeScript/vitest (`optivai-pi-plugin`), beads CLI, Open Brain (`open_brain.py` / brain HTTP), the existing dispatch-gate + loop runner, git worktrees, `pytest`.

---

## Context

Two recons (sourced, in this session's brain under project `gastown-mayor-rebuild`) established:

1. **We already sit on the Gas Town *foundation*** — real beads, molecules, a verified drain loop (`loop_runner.py`/`loop-runner.ts`), the dispatch-gate, propulsion. The missing ~80% is the **Mayor/colony orchestration superstructure**.
2. **Upstream beads itself migrated off JSONL onto Dolt;** Gas Town hard-requires a Dolt SQL server. Adopting it wholesale meant a second storage engine, a per-project beads split (undoing our unification), and a Tier-I no-sandbox/shared-fs/git-push colony. **Rejected.**
3. **Gas Town *abandoned* branch-per-worker for task state** (`docs/design/dolt-storage.md`: "all agents write directly to `main`… eliminates the former branch-per-worker strategy"). So the branch/merge feature is unnecessary at the reference source — we match the converged single-writer design.

**Decision (Chris, 2026-06-21):** Build the Mayor on our substrate (Path B). Gas Town is MIT — used as design blueprint only. No Dolt. No task-state merge engine.

## Scope Boundaries (non-negotiable)

- **`optivai-builder` (the OptivAI product, a Pi fork) is OFF-LIMITS.** Zero edits in this effort. Only Chris's dedicated agents touch it. This plan lives entirely in `optivai-claude-plugin` and `optivai-pi-plugin`.
- **Both plugins reach feature parity** (Chris's standing rule). Pi mirrors Claude.
- **No new storage engine.** No Dolt. State = beads (tasks) + Brain (ledger). 
- **No merge engine** for task state (single-writer Mayor makes it moot).

## Optimization Attractors (Chris's words)

- **Token efficiency** — Opus only for orchestration/design/hard-coding; Sonnet for implementation; Haiku for mechanical busy-work. The Mayor sizes the town to *our* token budget (no 20–30-agent "cash guzzler").
- **Security** — copy Gas Town's genuinely-good primitives; never copy its soft-convention defaults; sandbox-by-default.
- **Accuracy / review** — every code bead gates on a real V (test/build exit 0) before close; spec-reviewer → quality-reviewer on each wave.
- **Parity** — Claude and Pi stay in lockstep.

---

## Role Model — Gas Town → Ours

| Gas Town role | Responsibility | Our equivalent |
|---|---|---|
| **Mayor** (coordinator) | Decide what to dispatch, to whom; track in-flight | The extended loop runner — **single-writer** orchestrator over the bead molecule |
| **Polecat** (worker) | Execute one bead in isolation | A tier-routed `Task`/subagent dispatch through the dispatch-gate, in a git worktree |
| **Refinery** (merge) | Serialize code merges to main; batch-then-bisect | A merge step behind a `pg_advisory_lock` / file-lock; git-worktree squash; bisect on test failure |
| **Witness** (health) | Detect stuck/zombie workers; respawn/escalate | Reconciler: **mechanical detect → separate AI judge** decides kill/respawn (anti-"murder-spree") |
| **Deacon** (watchdog) | Re-derive state; re-dispatch stranded work | Folds into the reconciler heartbeat in v1 |
| Dog/Boot/Crew | zoo | **Dropped** |

**"Thin scheduler + smart agent + CLI" split** (their core insight) maps 1:1: loop runner = dumb scheduler; tier-routed subagents = intelligence; beads verbs + dispatch-gate = the CLI/contract. **"Discover, don't track"**: the Mayor re-derives in-flight state each tick from `beads ready` + live subagent count + worktree state — no separate registry to corrupt.

---

## Phased Plan

**Recommended v1 = the working Mayor (the heart).** Refinery-merge and sandbox-default are v2. Rationale: ship + validate the bounded concurrent coordinator first (mirrors how we built the loop runner — core first, extend later); keeps Opus-token spend bounded; gives Chris something runnable to dry-run.

### v1 — Coordinator Core (this plan's primary deliverable)

| Phase | What | Why it's v1 |
|---|---|---|
| **P0 — Capacity governor** | Extend the loop runner from single-bead drain → **bounded concurrent dispatch**: dispatch all `ready` beads up to `max_workers`, and as workers finish, dispatch newly-unblocked beads. Crash-aware slot accounting (a dead worker's slot stays *occupied* until resolved — no silent capacity leak). | The minimum that makes it a *colony* not a single stream |
| **P1 — Worker dispatch & contract** | Role-typed worker dispatch through the dispatch-gate (objective + bead id + paths-not-content + termination + output contract), tier-routed, in a per-worker git worktree. Workers **return results**; the Mayor commits. | The single-writer property + isolation |
| **P2 — Reconciler (Witness-lite)** | Each heartbeat: re-derive live state from beads + process liveness; mechanical **detector** emits a "stuck/crashed" event; a separate **AI judge** decides kill/respawn (never the detector itself). Guard-ladder (terminal-state / stale-hook / spawning-window / TOCTOU) to avoid false-kills. | Robustness; the hardest correctness part |
| **P3 — State + observability** | Extend `~/.claude/loop-state.json` schema to multi-worker (active workers, per-worker bead, capacity). Statusline (Claude) + widget (Pi) show the town. Ledger: Mayor records dispatch/verify/close transitions to the Brain with provenance. | Visibility (extends the obs layer we already shipped) |
| **P4 — Parity + hardening** | Port the Python engine to TS (Pi); parity test corpus identical verdicts; governor bounds (`--max-workers`, `--budget-tokens`); dry-run gate. | Chris's parity rule |

### v2 — Refinery + Sandbox (separate plan, after v1 validates)

- **Refinery-merge**: batch-then-bisect, anti-starvation scoring (port the formula), push-lock, typed-conflict→relabel-respawn.
- **OS-sandbox-by-default**: per-worker sandbox + CN-scoped push proxy (the "be better than Gas Town" upgrade).
- **Full Witness/Deacon** split if v1's folded reconciler proves insufficient.

---

## Bead Graph (v1 — to be created on validation, IDs filled at `beads create`)

```
M0 [epic: mayor-orchestration]
        │
   ┌────┴─────┐
  P0.1       P0.2 ......... capacity governor (design + impl, Python)
   │           │
   └─────┬─────┘
         ↓
       P1.1 → P1.2 ........ worker dispatch (gate-compliant) + worktree isolation
         │
         ↓
       P2.1 → P2.2 → P2.3 .. reconciler: detector / AI-judge / guard-ladder
         │
         ↓
       P3.1 → P3.2 ........ multi-worker loop-state schema + statusline/widget + Brain ledger
         │
         ↓
   ┌─────┴──────┐
  P4.1 (Pi port) P4.2 (parity corpus)
         │
         ↓
       P5.1 spec-reviewer gate → P5.2 quality-reviewer gate → P5.3 dry-run + --once proof → P6.1 close-out + brain retro
```

Each bead carries: exact files, acceptance criteria, and a **resolvable V** (a real `pytest`/`vitest`/`tsc`/dry-run command whose exit 0 gates close). Per-bead bite-sized TDD steps are produced at execution time by the subagent-driven-development + TDD skills — not pre-spelled here (epic-level plan). Labels: `epic:mayor-orchestration` + `repo:optivai-claude-plugin` / `repo:optivai-pi-plugin`.

### Bead registry (v1, proposed)

| Bead | Title | Depends | Tier | V (close-gate) |
|---|---|---|---|---|
| P0.1 | Capacity-governor design note | M0 | Opus | doc exists + reviewed |
| P0.2 | Bounded concurrent dispatch + crash-aware slots (Python) | P0.1 | Sonnet | `pytest test_governor.py` exit 0 |
| P1.1 | Role-typed worker dispatch through dispatch-gate | P0.2 | Sonnet | `pytest test_dispatch.py` exit 0 |
| P1.2 | Per-worker git-worktree isolation + single-writer commit | P1.1 | Sonnet | `pytest test_worktree_isolation.py` exit 0 |
| P2.1 | Mechanical stuck/crash detector (emit event, never act) | P1.2 | Sonnet | `pytest test_detector.py` exit 0 |
| P2.2 | AI-judge decision step (kill/respawn) separate from detector | P2.1 | Sonnet | `pytest test_judge.py` exit 0 |
| P2.3 | False-kill guard-ladder (terminal/stale/spawning/TOCTOU) | P2.2 | Sonnet | `pytest test_guards.py` exit 0 |
| P3.1 | Multi-worker `loop-state.json` schema + statusline/widget | P2.3 | Sonnet | `pytest test_state.py` + statusline render proof |
| P3.2 | Brain ledger: record dispatch/verify/close transitions w/ provenance | P3.1 | Sonnet | `pytest test_ledger.py` exit 0 |
| P4.1 | Port engine to TS (Pi parity) | P3.2 | Sonnet | `vitest mayor` + `tsc --noEmit` clean |
| P4.2 | Parity corpus — identical verdicts both harnesses | P4.1 | Sonnet | parity test exit 0 |
| P5.1 | spec-reviewer gate (all v1 changes) | P4.2 | Sonnet | review GREEN |
| P5.2 | quality-reviewer gate | P5.1 | Sonnet | review GREEN |
| P5.3 | Dry-run + `--once` live proof (real bounded town) | P5.2 | Opus | dry-run coherent + `--once` closes a real bead on V exit 0 |
| P6.1 | Close-out + brain retro | P5.3 | Opus | all beads CLOSED |

---

## Security — copy / don't-copy (from the blueprint review)

**COPY (genuinely well-built) — fold into P1/P2/v2:**
- Argv-slice **allowlist exec** (no `sh -c <string>` of agent-derived input).
- CN-scoped git-push **ref-validation that parses the wire protocol** (v2 proxy).
- **Minimal-env** subprocess (HOME+PATH only; never forward tokens).
- Rate / body / concurrency caps.

**NEVER COPY (their soft-convention defaults):**
- No-auth root Dolt server (we have no Dolt — N/A, but the lesson: never bind a store unauthenticated).
- Shared-filesystem default-trust → **we sandbox by default** (v2).
- Default shell-exec of config strings.
- Unauthenticated admin/cert API.
- "Trust levels" that don't actually gate.

**Be better:** OS-level worker isolation is the **default** (v2 P-sandbox), the one place Gas Town deferred and we lead.

## The genuinely hard parts (named honestly)

1. Crash-aware slot accounting — a dead worker's slot stays occupied until resolved (no silent capacity leak), without Gas Town's tmux-scan.
2. Convergent "discover, don't track" reconciliation with the guard-ladder to avoid false-kills.
3. Detector-vs-judge separation without the judge becoming a token sink.
4. (v2) Refinery batch-then-bisect + push-lock + conflict recovery.
5. (v2) OS sandbox-by-default + CN-scoped push proxy.

## CI / Regression Guards

- Governor: capacity never exceeds `max_workers`; crashed slot never silently frees (property tests).
- Single-writer invariant: a test asserts workers cannot mutate bead status directly (only the Mayor path).
- Parity: identical-verdict corpus across Python/TS (the dispatch-gate parity pattern, reused).
- Dispatch-gate compliance: every composed worker prompt passes `evaluate_dispatch().compliant`.

## Rollback Plan

Pure-additive on both plugins; `git revert` any phase. No production system, no Builder, no data migration. The Mayor reads beads/Brain we already run; reverting leaves the existing single-stream loop runner intact.

## Out of Scope

- `optivai-builder` (off-limits).
- Dolt / any new storage.
- Task-state merge engine.
- The 20–30-agent default colony / Wasteland federation / PR-provider abstraction.
- v2 items (refinery, sandbox-default, full Witness/Deacon) — separate plan after v1 validates.

## Open Forks (need Chris's validation before `beads create`)

1. **v1 scope** — ship the coordinator core (P0–P4) now, refinery+sandbox as v2? (recommended) — or fold v2 into one epic?
2. **Parity sequencing** — Claude-first per subsystem then Pi port (P4, recommended, de-risks the design once) — or build both in lockstep per bead?

## Execution Handoff

On validation: create the v1 bead molecule, dry-run for coherence, then **subagent-driven development** (Opus orchestrates here; Sonnet implements per bead; spec→quality review per wave; worktree isolation for any >2 parallel implementers). Nothing builds until Chris green-lights scope + sequencing.
