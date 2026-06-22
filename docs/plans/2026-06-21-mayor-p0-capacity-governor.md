# P0.1 — Capacity Governor Design Note

> Bead: `fblai-ekrtq` (epic `mayor-orchestration`). Implemented by P0.2 (`fblai-nag4a`).
> Extends `optivai-claude-plugin/scripts/loop_runner.py` (1040 lines). Pi port is P4.1.

## What this builds

Generalize the loop runner from **single-bead sequential drain** to a **bounded-concurrent Mayor**: dispatch up to `max_workers` ready beads at once, and as workers finish, dispatch newly-unblocked beads — while keeping the **Mayor as the single writer** to bead state and never silently freeing a crashed worker's slot.

## The single-writer invariant (the keystone)

Gas Town abandoned branch-per-worker because concurrent writers to task state are the hard problem. We sidestep it: **only the Mayor (main thread) mutates bead status.** Workers run the slow, read-only-w.r.t.-beads work (dispatch the subagent + run the verify command) and **return a result struct**. The Mayor reads results and performs every `beads update`/`close`.

- Worker callable returns `{bead_id, dispatch_result, verify_exit, error}`. It calls **no** `beads_close` / status mutation.
- Mayor main thread: `select ready → mark in_progress → submit to pool`; on completion `→ close (verify_exit==0) or leave-open`. All status writes here.
- Running `verify` inside the worker is allowed — it's a test command (writes only to the worker's isolated worktree, P1.2), not bead state. This keeps the slow path parallel while writes stay serialized.
- **Enforced in test:** a fake worker that attempts `runners.beads_close` fails the test; `close_if_verified` is reachable only from the Mayor path.

## Concurrency primitive

`concurrent.futures.ThreadPoolExecutor(max_workers)` — stdlib, no new deps. Threads (not async) because the existing `runners.dispatch`/`runners.verify` are blocking subprocess calls; a thread pool wraps them without rewriting the injection model. The `Runners` dataclass stays the injection seam so tests use fakes exactly as today.

## Data structures (added to loop_runner.py)

```python
LOOP_MAX_WORKERS: int = int(os.environ.get("OPTIVAI_LOOP_MAX_WORKERS", "4"))

@dataclass
class WorkerHandle:
    bead_id: str
    future: "Future"
    model: str
    started_at: float        # passed in from caller; Date.now-free per loop convention

# In the Mayor loop, three sets gate capacity:
active: dict[str, WorkerHandle]    # in-flight, slot occupied
recovery_blocked: set[str]         # crashed/timed-out; slot STILL occupied (no silent free)
# free = max_workers - len(active) - len(recovery_blocked)
```

**Crash-aware accounting is the whole point:** a timed-out/crashed worker moves `active → recovery_blocked`, the bead stays `in_progress` (never auto-closed, never auto-redispatched in P0), and **`recovery_blocked` counts against capacity.** Capacity is never silently reclaimed. P2's reconciler (detector→AI-judge) is what later clears `recovery_blocked`; in P0 it only accumulates and can trigger a stop.

## Mayor loop algorithm

```
run_mayor_loop(cfg, runners):
  pool = ThreadPoolExecutor(cfg.max_workers)
  active, recovery_blocked = {}, set()
  summary = RunSummary()
  while True:
    # 1. governor stop-check (extended should_continue)
    cont, reason = should_continue(summary, cfg, active, recovery_blocked)
    if not cont: break
    # 2. fill free slots from the ready set
    ready = runners.beads_ready(cfg.molecule)          # Mayor reads
    free = cfg.max_workers - len(active) - len(recovery_blocked)
    for bead in pick(ready, free, exclude=active.keys() | recovery_blocked):
        runners.beads_update(bead.id, "in_progress")   # Mayor writes
        h = WorkerHandle(bead.id, pool.submit(_worker, bead, cfg, runners), route_model(bead), now)
        active[bead.id] = h
    if not active:                                     # nothing running, nothing ready
        break                                          # queue-empty stop
    # 3. wait for ANY worker to finish (bounded by iter timeout)
    done = wait_first_completed(active, timeout=cfg.iter_timeout_s)
    for bead_id in done:
        h = active.pop(bead_id)
        res = h.future.result_or_timeout()
        if res.timed_out or res.error:
            recovery_blocked.add(bead_id)              # slot stays occupied; bead stays in_progress
            summary.failed += 1
        elif res.verify_exit == 0:
            runners.beads_close(bead_id)               # Mayor writes — close ONLY on verify exit 0
            summary.closed += 1
        else:
            summary.failed += 1                        # leave open; reconciler/next round may retry
        runners.brain_capture(...)                     # ledger event (P3.2 enriches)
  pool.shutdown(wait=False)
  return summary

_worker(bead, cfg, runners):                           # runs in a thread; NEVER mutates bead status
  prompt = compose_dispatch(bead, ...)                 # already gate-compliant (raises if not)
  dispatch_result = runners.dispatch(prompt, route_model(bead), cfg.iter_timeout_s)
  vcmd = resolve_verify_cmd(bead, cfg.verify_cmd)
  verify_exit = runners.run_verify(vcmd, cfg.verify_timeout_s) if vcmd else 0
  return WorkerResult(bead.id, dispatch_result, verify_exit, error=None)
```

`pick(ready, free, exclude)` deterministically takes up to `free` beads via the existing `select_next` ordering (priority then id), skipping any already active/recovery_blocked.

## Governor (extend `should_continue`)

Add the concurrency-aware stops to the existing four (max-iterations, budget, no-progress, queue-empty):

- **no-progress** now means: no bead closed across `LOOP_NOPROGRESS_K` consecutive completion-rounds **and** `active` is empty. A loop with workers still running is never "no progress."
- **capacity-exhausted-by-stuck-workers** (new): `len(recovery_blocked) >= max_workers` and `active` empty and no ready beads → stop with that reason. Surfaces a wedged town instead of spinning.
- max-iterations now bounds **completion-rounds**, not single beads.

## Integration points (exact)

- `loop_runner.py:55-64` — add `LOOP_MAX_WORKERS` beside the other `LOOP_*` constants.
- `RunConfig` (`:97`) — add `max_workers: int = 1`. **Default 1 preserves today's behavior** (sequential == a 1-worker town), so every existing test stays green.
- `Runners` (`:115`) — add `beads_update(bead_id, status)` to the injection seam (Mayor needs to set `in_progress`); `make_live_runners` (`:291`) wires `beads update <id> --status`.
- `run_loop` (`:779`) — when `max_workers == 1`, keep the existing sequential path verbatim; when `> 1`, dispatch to the new `run_mayor_loop`. (Or fold sequential into the pool path with `max_workers=1` and delete the old loop only if parity tests prove identical — prefer keeping both initially.)
- `should_continue` (`:655`) — extend signature with `active`, `recovery_blocked`; add the two new stop reasons.
- `_build_arg_parser` (`:940`) — add `--max-workers N`.
- State (`write_loop_state`/`_build_state` `:307-383`) — P3.1 extends to multi-worker; P0 just threads the active/recovery_blocked counts through so P3.1 can surface them.

## Test plan (what P0.2 must prove — all with injected fakes)

1. `test_capacity_never_exceeds_max_workers` — fake dispatch blocks on an event; assert `len(active) <= max_workers` at all times.
2. `test_fills_freed_slots_with_newly_unblocked` — a 3-bead chain + 2 independents, max_workers=2; assert a freed slot picks up the next ready bead.
3. `test_crashed_worker_slot_stays_occupied` — fake worker raises/times out; assert bead stays `in_progress`, `recovery_blocked` contains it, and capacity does **not** free (no redispatch of that bead).
4. `test_single_writer_invariant` — fake worker that calls `beads_close` → test fails; assert only the Mayor path closes.
5. `test_close_only_on_verify_exit_0` — verify_exit=1 leaves bead open; =0 closes. (Reuse existing `close_if_verified` semantics.)
6. `test_max_workers_1_matches_sequential` — parity: a fixed molecule run at max_workers=1 produces the same closed/failed counts as the current `run_loop`.
7. `test_capacity_exhausted_by_stuck_workers_stops` — fill recovery_blocked to max; assert stop reason.

V (close-gate for P0.2): `pytest scripts/tests/test_governor.py -q` exit 0, **and** the existing loop_runner test suite stays green (no regression at max_workers=1).

## Out of scope for P0

Worker dispatch *content* / worktree isolation = P1. Reconciler clearing `recovery_blocked` (detect→judge) = P2. Multi-worker state surfacing in statusline/widget = P3. This bead only delivers the bounded-concurrent governor + crash-aware slots + single-writer write-path, with `max_workers=1` backward-compatible.
