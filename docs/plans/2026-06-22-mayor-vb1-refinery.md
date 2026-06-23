# VB1 — Refinery Design Note (batch-then-bisect + merge-slot + scoring + conflict→re-implement)

> Bead: `fblai-3ztby` (epic `mayor-v2`). Implemented by VB2 (`pytest scripts/tests/test_refinery.py` exit 0).
> Extends `run_mayor_loop`'s VA0b serial merge-on-pass (`scripts/loop_runner.py:1920-2047`).
> Builds on VA0b's named-branch worktree lifecycle (`_live_worktree_create` / `_live_merge_worktree_branch` / `_live_worktree_teardown`) and the `_MERGE_LOCK` from `loop_runner.py:97`. Pi parity is VD1.

## What this builds

VA0b shipped a **minimal serial Refinery**: on `verify_exit == 0` the Mayor takes `_MERGE_LOCK`, runs one `git merge --no-ff mayor/<bead_id>`, then closes the bead (merge OK) or leaves it open and discards the code (merge conflict). Correct, but one branch at a time, with no recovery story beyond "drop it."

VB2 upgrades that into Gas Town's converged **Refinery** — a *single merge actor* that:

1. **batches** all currently-V-passed `mayor/<bead_id>` branches and tries to merge them in one cycle,
2. **bisects** to isolate the offender when a batch goes red, so good branches still land,
3. **scores** the merge queue so an old/often-retried branch never starves behind newer arrivals,
4. on a genuine conflict, **relabels the bead and re-dispatches** (re-implement against the advanced working branch) instead of silently discarding.

This is the v1→v2 step the mayor-v2 plan calls out (line 38): *"a minimal serial Refinery; VB2 upgrades it to GT's batch-then-bisect."*

## Why batch (the Gas Town lesson)

With N concurrent workers each producing a `mayor/<bead_id>` branch, serial one-at-a-time merge is correct but creates head-of-line latency: every branch pays a full `--no-ff` merge **plus** (in v2) a re-verify of the integrated tree. GT's Refinery amortizes this — merge K branches, verify the *combined* result **once**. Best case: K beads close for one verify. The cost only reappears when the batch is red, and bisect bounds it to `O(log K)` verifies to find the culprit while the innocent branches still land.

GT's "push-lock" (serialize pushes to a shared remote `main`) maps to **our merge-slot**: we merge into a **local working branch**, not a remote, so the slot is `_MERGE_LOCK` held around the whole batch cycle. No remote, no push proxy — same serialization guarantee.

## The merge-slot (single merge actor)

`_MERGE_LOCK` today is held around *one* `git merge`. VB2 promotes it to a **merge slot**: held around the whole batch-then-bisect cycle. Only one batch cycle runs at a time; workers keep dispatching in their worktrees concurrently and, on V-pass, **enqueue their branch coordinates** into a `MergeQueue` that the Refinery drains. Workers never hold the slot and never merge — the **single-writer invariant is unchanged** (workers return coordinates; only the Mayor main thread mutates the working branch and bead state).

## Data structures (added to loop_runner.py)

```python
LOOP_BATCH_MAX: int = int(os.environ.get("OPTIVAI_LOOP_BATCH_MAX", "8"))
LOOP_REFINERY_ATTEMPTS_MAX: int = int(os.environ.get("OPTIVAI_LOOP_REFINERY_ATTEMPTS_MAX", "2"))

@dataclass
class MergeCandidate:
    bead_id: str
    branch_name: str               # "mayor/<bead_id>"
    worktree_path: str             # Mayor tears down after the merge decision
    model: str
    verified_at: float             # monotonic, passed in (no Date.now inside the runner)
    attempts: int = 0              # refinery re-implement count (anti-loop)

# The Refinery drains this each cycle; workers (V-passed) append to it.
merge_queue: list[MergeCandidate]
```

## Scoring (anti-starvation) — port the GT formula

Order candidates so the **longest-waiting / most-retried / highest-priority** branch merges first, and no branch waits forever behind a stream of newer arrivals:

```
score(c, now) = W_age   * (now - c.verified_at)      # wait time → older first
              + W_retry * c.attempts                  # already bounced → prioritize
              + W_prio  * bead_priority(c.bead_id)    # molecule priority
```

Higher score merges first; deterministic tie-break by `bead_id`. **Pure function**: `now` is passed in, no `random`/`time.monotonic()` inside — same discipline as the P2 detector/guards, so the orderer is fully deterministic in tests. Weights are constants (`W_age`, `W_retry`, `W_prio`) tuned so a single retry or a few minutes of waiting outranks a fresh arrival.

## The batch-then-bisect algorithm

```
refine(queue, working_branch, runners, now) -> list[Outcome]:
    if not queue: return []
    batch = order_by_score(queue, now)[:cfg.batch_max]     # anti-starvation, bounded
    with _MERGE_LOCK:                                       # the merge slot — Mayor only
        snapshot = runners.git_snapshot(working_branch)     # rollback point (rev-parse HEAD)
        if runners.merge_batch(batch):                      # sequential --no-ff of each branch
            if runners.run_verify(cfg.verify_cmd) == 0:     # ONE verify for the whole batch
                return [Merged(c) for c in batch]           # best case: K close for 1 verify
        runners.git_reset(snapshot)                         # atomic rollback of the failed batch
    return bisect(batch, working_branch, runners, now)      # isolate the offender(s)
```

`bisect(batch, ...)`:
- **len == 1** → this branch is the culprit. It either won't apply (`merge_batch` returned a conflict) or fails V on its own → **conflict→re-implement** (below).
- **len > 1** → split in half. Merge+verify the first half under the slot; the green half are `Merged` (they close), recurse `bisect` into the red half. A clean half merges and stays; only the failing partition is split further. Innocent branches are **never penalized** for a neighbor's failure — a branch that was merged green in a sub-batch closes; one that returns to the queue is **re-scored, not demoted**.

This bounds the worst case to `O(log K)` verifies (one offender) and degrades gracefully toward the VA0b serial path as the batch shrinks.

## Conflict typing → re-implement (not silent discard)

At the single-branch (`len == 1`) level there are two failure modes, both meaning *the working branch moved under the worker*:

1. **Textual conflict** — `git merge --no-ff` returns non-zero; the branch won't apply onto the advanced HEAD.
2. **Semantic conflict** — merge applies cleanly but solo V goes red because of interaction with peers that landed first.

VA0b's current behavior (`loop_runner.py:1925-1954`) drops the code and leaves the bead open with **no signal** — the next dispatch re-derives from scratch, unaware it's a rebase-against-moved-HEAD. VB2 instead:

- **Relabel** the bead `conflict:re-implement` and bump `refinery-attempts`, so the re-dispatch prompt knows to implement *against the current (merged) HEAD*, not greenfield.
- **Respawn (bounded)**: return the bead to the ready set; the Mayor re-dispatches a worker whose worktree is freshly branched off the **now-current** HEAD (it sees the merged peers). Bounded by `cfg.refinery_attempts_max` (default 2) to stop conflict→re-implement loops — the same respawn-cap shape as P2's `LOOP_MAX_RESPAWNS`. On cap exhaustion → **escalate**: leave open, ledger `refinery-exhausted`, surface to the operator (never spin forever).
- **Teardown** the stale worktree (discard the code) exactly as today.

Reuses two patterns already in the tree: the bead-relabel mechanism and P2's bounded respawn-cap.

## Invariants preserved (all CI-guarded, additive)

- **Single-writer** — only the Mayor runs `refine()`; workers only append `MergeCandidate`s. A worker calling `merge_batch`/`git_reset` is a test failure (mirrors `TestSingleWriterPreservedVA0b`).
- **Merge-slot serialization** — `_MERGE_LOCK` held around the whole cycle; the git index is never raced (extends `TestMergeSerializiation`).
- **Atomic rollback** — `git_snapshot` + `git_reset` mean a red batch leaves the working branch byte-identical to its pre-batch state; no partial merge ever persists.
- **Determinism** — scoring/ordering pure; `now` injected; no `random`/`Date.now` in the orderer.
- **Backward compatibility** — `batch_max == 1` reproduces the VA0b serial path exactly (one branch, one merge, one close), so every existing worktree/governor/rate-limit test stays green.

## Config / CLI (added to RunConfig)

- `RunConfig` (`loop_runner.py:183`) — `batch_max: int = 1`, `refinery_attempts_max: int = LOOP_REFINERY_ATTEMPTS_MAX`. **Default `batch_max=1` preserves today's behavior** (serial Refinery == a batch of one).
- `_build_arg_parser` — `--batch-max N`, `--refinery-attempts N`.
- Verify: the batch uses the molecule-wide `cfg.verify_cmd`; a single-bead batch still honors that bead's `verify:<cmd>` label (unchanged resolution).

## Integration points (exact, for VB2)

- `loop_runner.py:97` `_MERGE_LOCK` — update the comment from "held only during merge" to "the merge slot: held around the whole batch-then-bisect cycle."
- `run_mayor_loop` completion handler (`:1920-2047`) — replace the inline per-bead `with _MERGE_LOCK: runners.merge_branch(...)` with: on V-pass, **append a `MergeCandidate`** to `merge_queue`; once per tick (after the completion drain) call `refine(merge_queue, ...)`. Apply outcomes: close `Merged` beads (single-writer); relabel+return-to-ready the `ReImplement` culprits; ledger `escalated` on cap exhaustion. The crash/timeout/rate-limit branches (`:1855-1919`) are untouched.
- `Runners` (`:234`) — add injected seams `merge_batch(list[MergeCandidate]) -> bool`, `git_snapshot(branch) -> str`, `git_reset(snapshot) -> None`. Live wiring in `make_live_runners` (`:739`) drives git; tests use a fake real-git-in-`tmp_path` harness as `test_worktree_integration.py` already does. Keep `merge_branch` for the `batch_max==1` path.
- Ledger — add actions `batch-merge`, `bisect`, `conflict-reimplement`, `refinery-exhausted` beside the existing `verify-pass` / `merge-fail` / `close` (`_ledger_capture`).

## Test plan (what VB2's `test_refinery.py` must prove — injected fakes, real git in `tmp_path`)

1. **`batch_max=1` == VA0b serial** — same closed/failed counts; one merge per bead (parity guard).
2. **all-green batch** — K non-conflicting branches → one verify → K closed.
3. **one bad branch** — bisect isolates it; the K−1 good branches close, the culprit does not.
4. **textual conflict (solo)** — `conflict:re-implement` label set + bead returned to ready + worktree torn down + nothing reaches the working branch.
5. **semantic conflict** — merge clean, batch V red, solo V red → same re-implement path.
6. **anti-starvation** — an old / once-retried branch outscores newer arrivals; assert it merges first.
7. **refinery-attempts cap** — a perpetually-conflicting bead is re-implemented up to the cap, then escalated (not infinite).
8. **single-writer** — a fake worker that calls `merge_batch`/`git_reset` fails the test.
9. **merge-slot serialization** — concurrent batch cycles never overlap (extends `TestMergeSerializiation`).
10. **rollback atomicity** — after a red batch, `git rev-parse HEAD` equals the pre-batch snapshot.

V (close-gate for VB2): `pytest scripts/tests/test_refinery.py -q` exit 0 **and** the existing worktree/governor/rate-limit suites stay green (the `batch_max=1` backward-compat path).

## Out of scope (VB1/VB2)

- OS-sandbox (cut — product-context bleed; worktrees are the GT-faithful isolation).
- Remote push / push-proxy (we merge to a local working branch; merge-slot replaces push-lock).
- Rebase-instead-of-merge (we keep `--no-ff`; rebase is a future optimization, not this note).
- Cross-repo and multi-host Refinery.
- Pi parity port = VD1 (this note is Claude-first; Pi mirrors after VB2 validates).
