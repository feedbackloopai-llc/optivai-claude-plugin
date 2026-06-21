# P2 — Reconciler Design Note (detector → guard-ladder → AI-judge)

> Beads: P2.1 `fblai-hvnoq` (detector), P2.2 `fblai-ch1dj` (judge), P2.3 `fblai-e0zwe` (guards).
> Extends `run_mayor_loop` in `scripts/loop_runner.py`. Builds on the `active` / `recovery_blocked` sets from P0.2.

## The problem

P0.2 moves crashed/timed-out workers into `recovery_blocked` and leaves them there forever — the slot stays occupied until `capacity-exhausted` stops the loop. P2 adds the intelligence that **clears `recovery_blocked`** (and catches *hung-but-not-crashed* workers) so the town self-heals instead of wedging.

## The one rule that shapes everything (Gas Town's hard-won lesson)

**Mechanical detection must NEVER act.** Gas Town's history: a detector that auto-killed caused a "Deacon murder spree." So the design is a strict three-stage pipeline with separation of concerns:

```
detect (pure, mechanical) → guard-ladder (pure, suppress false-positives) → AI-judge (reasoned decision) → Mayor executes (single-writer)
```

Each stage is independently testable; only the **Mayor** (main thread) ever mutates bead state or slots.

## Stage 1 — Detector (P2.1, pure/mechanical, emits events, never acts)

A pure function over a snapshot — no I/O, no AI, no mutation:

```python
@dataclass
class StuckCandidate:
    bead_id: str
    kind: str            # "crashed" | "hung"
    runtime_s: float     # now - started_at
    model: str

def detect_candidates(active: dict[str, WorkerHandle],
                      recovery_blocked: set[str],
                      now: float,                    # passed in (no time.monotonic inside)
                      stuck_threshold_s: float) -> list[StuckCandidate]:
    # crashed: already in recovery_blocked (future errored/timed-out in completion handling)
    # hung:    in `active`, future not done, runtime > stuck_threshold_s
```

`LOOP_STUCK_THRESHOLD_S` (env `OPTIVAI_LOOP_STUCK_THRESHOLD_S`, default e.g. `1800`). The detector returns candidates; it does not touch beads, slots, or the pool.

**P2.1 tests (`test_detector.py`):** crashed→candidate; hung-past-threshold→candidate; running-under-threshold→NOT a candidate; healthy active workers→empty; detector mutates nothing (assert sets unchanged).

## Stage 2 — Guard-ladder (P2.3, pure, suppresses false-positives)

Each candidate runs the ladder; **any** guard that fires drops it (it is NOT stuck — a race, not a failure). Ladder order (cheapest/most-decisive first):

1. **terminal-state** — bead already `done`/`closed` (worker finished, Mayor hasn't reaped yet) → drop.
2. **stale-hook** — bead no longer assigned to this worker (closed/reassigned) → drop.
3. **spawning-window** — `runtime_s < LOOP_SPAWNING_WINDOW_S` (default `300`) → still warming up → drop. (Applies to `hung`, not `crashed`.)
4. **TOCTOU re-check** — re-read worker/bead liveness at the moment of decision; if the future is now done or the bead now closed → drop.

```python
def survives_guards(c: StuckCandidate, bead_status: str, future_done: bool,
                    assigned_worker: str|None, now_runtime_s: float,
                    spawning_window_s: float) -> bool: ...
```

Pure: takes the current observed state as args (injected), returns bool. No I/O.

**P2.3 tests (`test_guards.py`):** one test per guard firing (terminal/stale/spawning/TOCTOU each drops); a genuinely-stuck candidate that passes all four survives; spawning-window applies to `hung` but a `crashed` worker is not spared by it.

## Stage 3 — AI-judge (P2.2, reasoned decision, injected seam)

Only **guard-surviving** candidates reach the judge — this is the token-sink mitigation (the judge is never invoked for healthy or racing workers, and never every tick for the same worker once decided). The judge is an injected runner so tests use a fake:

```python
# Runners gains:
judge: Optional[Callable[[StuckCandidate, dict], str]] = None   # → "kill" | "respawn" | "wait"
```

- Live `judge` = a **cheap-tier (haiku)** dispatch with a tight prompt (candidate context: bead id, kind, runtime, last output tail) returning one token. Default to `"wait"` on any judge error/timeout (fail-safe: never auto-kill on judge failure).
- `kill` → Mayor marks the bead failed/leaves it open, frees the slot, clears from `recovery_blocked`/`active`.
- `respawn` → Mayor clears the slot and lets the bead return to the ready set for redispatch (bounded by a per-bead respawn cap, `LOOP_MAX_RESPAWNS` default `1`, to prevent respawn loops).
- `wait` → leave as-is; re-evaluated next tick (but the spawning-window/threshold mean it won't be re-judged immediately).

Token-sink guards: judge only on guard-survivors; once a candidate is judged `kill`/`respawn` it leaves the candidate set; `wait` decisions are rate-limited by the stuck-threshold so the same hung worker isn't re-judged every loop tick.

**P2.2 tests (`test_judge.py`):** fake judge returning `kill` → Mayor frees slot + bead not closed (left for retry/failed); `respawn` → bead redispatchable + respawn-cap enforced (second respawn denied); `wait` → state unchanged; judge raises/None → treated as `wait` (fail-safe, no auto-kill); judge is invoked ONLY for guard-survivors (a guarded-out candidate never reaches the fake judge — assert call count).

## Integration into `run_mayor_loop`

Add a `reconcile(active, recovery_blocked, cfg, runners, now)` step called **once per loop tick**, after slot-filling and before/with the completion wait. It runs detect → guards → judge → and returns the Mayor's actions to apply (the function itself stays pure-ish; the Mayor applies the slot/bead mutations). Single-writer is preserved: `reconcile` decides, the Mayor (main thread) executes.

New config on `RunConfig`: `stuck_threshold_s`, `spawning_window_s`, `max_respawns`. New CLI args: `--stuck-threshold`, `--spawning-window`, `--max-respawns`.

## Global constraints (all three beads)

- Detector + guards are **pure functions** (no I/O, no AI, no mutation) — fully deterministic tests.
- Judge is **injected** (`Runners.judge`); fail-safe to `"wait"` on error; cheap tier in the live path.
- **Single-writer** unchanged: only the Mayor mutates beads/slots.
- No `time.monotonic()`/`random` inside pure functions — `now` is passed in.
- Existing suites stay green (`test_governor.py`, `test_dispatch.py`, `test_worktree_isolation.py`, `test_loop_runner.py`, `test_loop_state.py`).

## Why this is the hard part

The correctness risk is **false-kills** (killing a worker that was about to succeed) and **respawn loops** (kill→respawn→kill forever). The guard-ladder + TOCTOU re-check kill the first; the respawn cap kills the second; the detector-never-acts split means no mechanical path can murder a worker without a reasoned decision. Get these three right and the town self-heals without eating itself.
