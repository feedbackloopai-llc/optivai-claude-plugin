# T1 Design: recall_assembly Architecture + Token-Budget / Dedup / CCR Contracts

**Status:** DESIGN (T1 of `2026-06-18-native-recall-compression.md`). No code in this task.
**Gates:** T2 (`scripts/recall_assembly.py`) and T3 (semantic dedup in `open_brain.py search()`).
**Scope guard:** recall ranking / budget / dedup / rendering only. No auth, tenant, DEAS, or
crypto logic is designed here; existing fidelity guarantees (PROV-DM, trust envelope,
VF_epsilon, PII redaction, NAL stv) are treated as constraints and preserved (Section 5).

## 0. Grounding (verified against the real code, 2026-07-02)

| Fact | Source |
|---|---|
| `search(conn, query, user_id, limit=DEFAULT_SEARCH_LIMIT, threshold=DEFAULT_SIMILARITY_THRESHOLD, sort_by="similarity", thought_type=None, topics=None, people=None, date_from=None, date_to=None)` | `scripts/open_brain.py:3302` |
| Ranking: SQL `hybrid_score = vec_similarity*0.85 + keyword_boost*0.10 + time_decay*0.05`, `LIMIT %s` applied in SQL, then Python-side `vec_similarity >= threshold` filter | `open_brain.py:3388-3432` |
| Post-SQL passes, in order: (1) Hebbian boost `HYBRID_SCORE += HEBBIAN_BOOST_COEFFICIENT * effective_weight` gated by `SIMILARITY >= HEBBIAN_MIN_RELEVANCE_FLOOR` (0.30, line 2523) + re-sort; (2) `_annotate_provenance` (SUPERSEDED_BY penalty x `OPEN_BRAIN_SUPERSEDE_PENALTY` default 0.5, DISPUTED annotation) + re-sort; (3) reinforcement `UPDATE ... SET updated_at=NOW()` on every returned THOUGHT_ID; (4) `emit_replay_log` | `open_brain.py:3457-3568` |
| Result dict keys are UPPERCASED: `THOUGHT_ID, RAW_TEXT, SUMMARY, THOUGHT_TYPE, TOPICS, PEOPLE, ACTION_ITEMS, SOURCE, PROJECT, CREATED_AT, SIMILARITY, HYBRID_SCORE, KEYWORD_BOOST, TIME_DECAY, STV {"f","c"}, LOW_CONFIDENCE, EFFECTIVE_WEIGHT, PROMOTION_BOOST` + optional `SUPERSEDED_BY: [ids]`, `DISPUTED: {by, types}` | `open_brain.py:3427-3455, 3489-3501, 3275-3283` |
| The scored CTE does NOT select `embedding` - dedup cannot run on today's result rows without a SQL change | `open_brain.py:3388-3414` |
| Dedup today is exact-`THOUGHT_ID` only (hook `seen_ids` set; SQL rows are PK-unique anyway) | `auto_recall_hook.py:295-307`; candidate-tid keying at `open_brain.py:3228` |
| Thought IDs: `brain-{epoch}-{8hex}`; the hook's short-id is the LAST 8 chars (`SHORT_ID_CHARS = 8`) | `open_brain.py:759-763`, `auto_recall_hook.py:51` |
| Hook truncation today: blunt `summary[:SUMMARY_MAX_CHARS].rstrip() + "..."` at 200 chars, mid-sentence | `auto_recall_hook.py:50, 251-252` |
| Trust envelope: `<recalled-memory-data>` + `_ENVELOPE_PREFACE` + per-field `sanitize_untrusted_string` | `auto_recall_hook.py:154-334` |
| CCR retrieve primitive: `python3 open_brain.py --inspect <THOUGHT_ID>` -> `time_travel.inspect_latest`. **Verified gap:** `inspect_latest` returns `None` when no `brain.thought_versions` rows exist (captured but never snapshotted/updated) - see Section 4.3 | `open_brain.py:6465-6519`, `time_travel.py:192` |
| PII redaction runs at the capture / replay-log emitter boundary (`redact_pii`, gz-redact pipeline), BEFORE storage/egress | `open_brain.py:40-98, 158-160` |
| Pi bridge: `_run_from_pi` dispatches `op:"search"` with JSON params on stdin | `open_brain.py:5421` |
| `NAL_LOW_CONFIDENCE_THRESHOLD = 0.35`; `DEFAULT_SEARCH_LIMIT = 10`; `DEFAULT_SIMILARITY_THRESHOLD = 0.3` | `open_brain.py:837, 263-264` |

---

## 1. `recall_assembly.py` architecture

One new pure module. **Pure and deterministic: no I/O, no DB, no network, no clock reads,
no randomness, no new model, zero per-recall API cost.** Same inputs always produce the
same output (modulo the tiktoken-present vs tiktoken-absent environments, each of which is
internally deterministic). Callers (auto_recall_hook T5, context_primer T5, Pi T7b, the T4
projectors) do the I/O; this module only transforms lists of dicts into a rendered block.

### 1.1 Module constants (named defaults; T2 must use these exact names)

```python
DEFAULT_RECALL_TOKEN_BUDGET = 600     # env override read by CALLERS: OPEN_BRAIN_RECALL_TOKEN_BUDGET
SUMMARY_MIN_CHARS = 80                # per-atom summary floor for budget-derived caps
SENTENCE_BOUNDARY_CHARS = (".", "!", "?")   # boundary = one of these followed by space, or a newline
ELLIPSIS = "…"                        # 1 char; appended on any truncation
SHORT_ID_CHARS = 8                    # mirrors auto_recall_hook.py:51 (last 8 of THOUGHT_ID)
EXPAND_HINT = "↳ expand any memory: python3 open_brain.py --inspect <id>"
CHARS_PER_TOKEN_ESTIMATE = 4          # fail-open fallback divisor
```

Note on the env var: `assemble_recall` itself takes `token_budget` as a plain parameter
and never reads the environment (purity). The env override `OPEN_BRAIN_RECALL_TOKEN_BUDGET`
is resolved by the hook/caller layer (T5) and passed in.

### 1.2 Public API (exact signatures; T2 is gated on these)

```python
def count_tokens(text: str) -> int:
    """tiktoken cl100k_base count; on ANY import/encode failure return max(1, len(text) // 4).
    Lazy module-level singleton encoder; never raises."""

def summarize_to(text: str, max_chars: int) -> str:
    """Sentence-boundary-aware truncation. len(result) <= max_chars always (for max_chars >= 2).
    <= max_chars input returns unchanged (no ellipsis). Never raises."""

def project_fields(record: dict, keep: list[str]) -> dict:
    """Field projection. Returns a NEW dict (input never mutated):
    - keys in `keep`: copied verbatim
    - keys NOT in `keep` whose value is a list or dict: collapsed to f"{key}_count": len(value)
    - other non-keep keys: dropped
    - the record's id key ("THOUGHT_ID" or "thought_id"), if present, is ALWAYS retained
      even when absent from `keep` (CCR reversibility, invariant R2)."""

def assemble_recall(atoms: list[dict], token_budget: int = DEFAULT_RECALL_TOKEN_BUDGET) -> dict:
    """Relevance-ordered greedy token-fill over search()-shaped atom dicts (UPPERCASE keys).
    Order-preserving: NEVER re-ranks. Returns the assembly result dict (Section 1.4)."""
```

### 1.3 Pipeline stages inside `assemble_recall` (in order)

1. **Validate / fail-open.** `atoms` not a non-empty list of dicts -> return the empty
   result (`included=0, dropped=0, rendered="", lines=[]`). Atoms missing `THOUGHT_ID`
   are skipped (counted in `dropped`, id-less so not in `dropped_ids`).
2. **Reserve the CCR hint.** `hint_tokens = count_tokens(EXPAND_HINT)`;
   `available = token_budget - hint_tokens`. The hint is part of the budget.
3. **Relevance-ordered greedy fill** (Section 2.2) - walk atoms in the GIVEN order
   (search() already ranked them; assembly trusts that order), rendering each atom line
   (Section 1.5), summarizing to fit when needed, dropping when even the floor summary
   does not fit. The top-ranked atom is never dropped (Section 2.3).
4. **Sentence-aware summary** is the fitting mechanism inside stage 3, via
   `summarize_to` (Section 2.4). Replaces the hook's blunt 200-char cut.
5. **Field projection** is NOT applied inside `assemble_recall` (recall lines are built
   from a fixed field set, Section 1.5). `project_fields` is exported for the T4
   structured-output projectors (schemas in Section 1.6).
6. **CCR id-annotation.** Every line carries the atom's short-id; `rendered` ends with
   `EXPAND_HINT`; machine annotations carry FULL THOUGHT_IDs (Section 4).

### 1.4 Output shape (exact; T2 gated on these keys)

```python
{
  "lines": list[str],        # one per included atom, input (relevance) order preserved
  "rendered": str,           # "\n".join(lines) + "\n" + EXPAND_HINT   ("" when included == 0)
  "included": int,
  "dropped": int,            # atoms not included (budget-dropped + malformed)
  "dropped_ids": list[str],  # FULL THOUGHT_IDs of budget-dropped atoms (order preserved)
  "token_count": int,        # count_tokens(rendered); invariant B1: <= token_budget
  "expand_hint": str,        # EXPAND_HINT (present even if included == 0, for callers)
  "annotations": dict[str, dict],  # keyed by FULL THOUGHT_ID, per-atom:
      # {"summarized": bool,            # summary was truncated to fit
      #  "near_duplicate_count": int,   # passthrough from T3 field NEAR_DUPLICATE_COUNT, 0 if absent
      #  "near_duplicate_ids": list[str]}  # passthrough from T3, [] if absent
}
```

This is a superset of the parent plan's `{"lines","rendered","included","dropped","expand_hint"}`;
supersets are allowed, key renames are not.

### 1.5 Atom line format (recall path)

Built from the search() UPPERCASE fields only; format mirrors the hook's existing line so
T5 is a drop-in:

```
- {CREATED_AT[:10]} | {THOUGHT_TYPE} | {short_id}{dup}{flags} - {summary}
```

- `short_id` = `THOUGHT_ID[-SHORT_ID_CHARS:]` (display only; never a lookup key).
- `dup` = ` (+N similar)` when `NEAR_DUPLICATE_COUNT` > 0 (T3 annotation), else "".
- `flags` = concatenation, in this order, of ` [low-conf]` when `LOW_CONFIDENCE` is truthy,
  ` [disputed]` when `DISPUTED` present, ` [superseded]` when `SUPERSEDED_BY` present.
  These are the fidelity markers (Section 5); the full detail is one `--inspect` away.
- `summary` = `SUMMARY`, falling back to `RAW_TEXT`, passed through `summarize_to` with the
  budget-derived cap (Section 2.2). Missing both -> `"(no summary)"`.
- Assembly emits UNsanitized lines. The hook (T5) applies `sanitize_untrusted_string` per
  line AFTER assembly, exactly as `_format_atom_line` does today. Sanitization deltas
  (placeholder substitutions, one U+200C) are small and outside the budget measurement
  point, which is `rendered` as produced by `assemble_recall` (invariant B1). The
  envelope + preface (~40 tokens) are likewise the caller's overhead, not the block's.

### 1.6 Projection schemas (consumed by T4 via `project_fields`)

| Output | `keep` list | Collapsed (list/dict -> `{key}_count`) |
|---|---|---|
| `graph_search` record | `THOUGHT_ID, SUMMARY, THOUGHT_TYPE, CREATED_AT, HYBRID_SCORE, GRAPH_DEPTH, GRAPH_SOURCE, SUPERSEDED_BY, DISPUTED` | `TOPICS, PEOPLE, ACTION_ITEMS`, any link/edge arrays |
| `citation_walker` node | `THOUGHT_ID, SUMMARY, LINK_TYPE, DEPTH` | children/links arrays |
| `timeline` record | `THOUGHT_ID, CREATED_AT, SUMMARY, THOUGHT_TYPE` | `TOPICS, PEOPLE, ACTION_ITEMS` |

Every projected view ends with the same `EXPAND_HINT` line. Raw `--json` output stays
unprojected (T4 gates projection behind `--project`/`--full`, default project ON for the
text view only) - machine consumers see today's bytes.

---

## 2. The token-BUDGET contract

### 2.1 Token counting

- Primary: `tiktoken` `cl100k_base` (new dependency, `tiktoken>=0.7` in
  `scripts/requirements.txt`), encoder built lazily once per process.
- Fail-open fallback: on `ImportError` or ANY encoder exception,
  `count_tokens(text) == max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)`.
- Self-consistency, not oracle-truth: invariant B1 is stated against `count_tokens`
  itself. In fallback mode the count is an estimate; the invariant still holds because
  fill and final count use the same function.

### 2.2 Greedy-fill algorithm (normative)

```
available = token_budget - count_tokens(EXPAND_HINT)
used = 0
for atom in atoms (given order):                      # rank order; NEVER re-sorted
    line_full = render(atom, summary=full)            # Section 1.5, no truncation
    cost = count_tokens(line_full + "\n")
    if used + cost <= available:
        include(line_full); used += cost; continue
    # derive a per-atom char cap from what remains:
    remaining_tokens = available - used
    cap = max(SUMMARY_MIN_CHARS,
              remaining_tokens * CHARS_PER_TOKEN_ESTIMATE - len(fixed_prefix(atom)))
    line_sum = render(atom, summary=summarize_to(summary_text, cap))
    cost = count_tokens(line_sum + "\n")
    if used + cost <= available:
        include(line_sum, annotate summarized=True); used += cost; continue
    drop(atom)                                        # record in dropped_ids; keep scanning
```

First-fit by rank: a dropped atom does not stop the scan - a later (lower-ranked, shorter)
atom may still fit. This never displaces a higher-ranked atom: inclusion is decided
strictly in rank order, so by the time a lower-ranked atom is considered, every
higher-ranked atom already had first claim on the budget (invariant B3).

### 2.3 Top-atom guarantee

If `atoms` is non-empty and the rank-1 atom does not fit even at `SUMMARY_MIN_CHARS`,
it is force-fit: its summary is hard-cut (ellipsis appended, floor OVERRIDDEN downward)
to the largest length whose rendered line + hint fits `token_budget`. The budget invariant
(B1) dominates the floor. Consequence: `included >= 1` whenever `atoms` is non-empty and
`token_budget >= count_tokens(minimal_line + "\n" + EXPAND_HINT)`; below that degenerate
budget, the empty result is returned rather than a budget violation.

### 2.4 `summarize_to` (sentence-aware) - normative behavior

1. `len(text) <= max_chars` -> return `text` unchanged.
2. Else take `prefix = text[:max_chars - 1]` (reserving 1 char for `ELLIPSIS`), find the
   RIGHTMOST sentence boundary in it: a char in `SENTENCE_BOUNDARY_CHARS` followed by a
   space (or at end of prefix), or a newline. Cut just after the boundary punctuation,
   `rstrip()`, append `ELLIPSIS`.
   Plan-pinned example: `summarize_to("A. B. C.", 4) == "A.…"` (not `"A. B"`).
3. No boundary in the prefix -> hard cut: `prefix.rstrip() + ELLIPSIS`.
4. Guarantee: `len(result) <= max_chars` for all `max_chars >= 2`; for `max_chars < 2`
   return `text[:max_chars]`.

### 2.5 Budget parameter

`token_budget: int = DEFAULT_RECALL_TOKEN_BUDGET` (600). Callers resolve
`OPEN_BRAIN_RECALL_TOKEN_BUDGET` (int, clamped to `>= 50`) and pass it. `token_budget <= 0`
-> empty result, no crash.

### 2.6 Budget invariants (gate for T2)

| ID | Invariant | Test predicate |
|---|---|---|
| B1 | Never exceed budget | `assemble_recall(atoms, b)["token_count"] <= b` for all inputs (property-style over the T2 fixtures) |
| B2 | Fallback fail-open | with tiktoken import monkeypatched to fail, `count_tokens(t) == max(1, len(t)//4)` and B1 still holds |
| B3 | Never drop higher-relevance for lower | for the result, every included atom at input index j implies every input index i < j was either included or failed its own fit check at its turn (assert via instrumented fixtures: fat-atom-then-thin-atom cases) |
| B4 | Top atom never dropped | single atom larger than budget -> `included == 1`, summarized, B1 holds |
| B5 | Order preservation | the sequence of THOUGHT_IDs in `lines` is a subsequence of the input sequence |
| B6 | Purity/determinism | two calls with equal inputs return equal outputs; `atoms` list and its dicts are not mutated |

---

## 3. The DEDUP contract (T3, in `open_brain.py search()`)

### 3.1 Placement in search() and signature change

`search()` gains one backward-compatible parameter: `dedup: bool = False`.
CLI gains `--dedup` / `--no-dedup` (paired flags; default OFF so raw `--search` output is
byte-identical to today). Pi bridge `op:"search"` gains optional `"dedup": true|false`
(default false); the Pi `brainSearch` caller (T7b) passes true.

Pipeline position (grounded in the actual pass order at `open_brain.py:3457-3568`):

```
SQL rank + LIMIT  ->  threshold filter  ->  Hebbian boost + re-sort
  ->  _annotate_provenance (supersede penalty) + re-sort
  ->  [NEW] semantic dedup  ->  [NEW] truncate to `limit`
  ->  reinforcement touch (survivors only)  ->  emit_replay_log  ->  return
```

Dedup runs AFTER all scoring passes so survivor selection sees the FINAL `HYBRID_SCORE`
(including the Hebbian boost and the supersede penalty), and BEFORE the reinforcement
touch so collapsed atoms do NOT get their `updated_at` clock reset (a near-dup cluster
must not stay perpetually fresh as a block; only surfaced atoms reinforce).

### 3.2 Over-fetch (so dedup frees slots instead of just shrinking output)

When `dedup=True`, the SQL `LIMIT` parameter becomes
`min(limit * DEDUP_OVERFETCH_FACTOR, DEDUP_OVERFETCH_CAP)` with
`DEDUP_OVERFETCH_FACTOR = 3`, `DEDUP_OVERFETCH_CAP = 50`; after dedup, truncate survivors
to the caller's `limit`. When `dedup=False` the SQL is unchanged (byte-stability D7).
The Hebbian batch and `_annotate_provenance` operate on the over-fetched set (both are
single-round-trip batch queries already; n <= 50).

### 3.3 Embeddings for pairwise cosine

The scored CTE does not select `embedding` today (grounding table). When `dedup=True` the
CTE additionally selects `embedding::text`; rows parse it (pgvector text `[f1,f2,...]`,
`json.loads`-compatible) into a transient `_EMBEDDING` key. **The embedding is popped
before results leave `search()` - it never appears in `--json` output or the returned
dicts (invariant D6; 768 floats per row would bloat every consumer).** When `dedup=False`
the CTE is unchanged.

### 3.4 The collapse algorithm

```
DEDUP_COSINE = 0.92     # module constant next to HEBBIAN_* constants
survivors = []
for r in results (final rank order):
    if r has no parseable _EMBEDDING: survivors.append(r); continue     # fail-open D2
    for s in survivors (rank order):
        if s has _EMBEDDING and cosine(r, s) >= DEDUP_COSINE:
            s.NEAR_DUPLICATE_IDS.append(r["THOUGHT_ID"]); s.NEAR_DUPLICATE_COUNT += 1
            absorbed = True; break
    if not absorbed: survivors.append(r)
results = survivors[:limit]
```

O(n^2) pairwise over n <= 50 (bounded by DEDUP_OVERFETCH_CAP) with 768-dim dots - trivial
cost, no index needed. Cosine computed as dot/(norm*norm) in pure Python or via the
already-imported embedding stack; either is acceptable, the threshold semantics are what
is gated.

### 3.5 Survivor selection: highest final HYBRID_SCORE (first-in-rank), and why

The survivor of a near-duplicate cluster is the highest-ranked atom by final
`HYBRID_SCORE` (which is simply the first one encountered, since the list is sorted).
Ties (equal HYBRID_SCORE to 4dp) break by: higher `STV["c"]`, then more recent
`CREATED_AT`, then lexicographically smallest `THOUGHT_ID` (determinism).

Why rank and not recency or confidence as the primary key:
- **Rank already fuses everything the Brain knows about retrieval preference** - cosine
  relevance (0.85), keyword match (0.10), recency (time_decay, 0.05), Hebbian promotion
  (agent-asserted importance), and the supersede penalty (provenance). Choosing a
  different survivor would let a lower-relevance atom displace a higher-relevance one,
  violating the ordering invariant D3 and re-litigating the ranking policy inside dedup.
- **Recency as primary would double-count**: time_decay is already a scoring term, and a
  superseded-but-recent duplicate could beat the atom that superseded it.
- **stv.c as primary is orthogonal to the query**: a high-confidence but less-relevant
  duplicate would outrank the atom the user actually asked about. Confidence is real
  evidence weight, so it earns the FIRST tie-break slot - it informs survivor selection
  but never overrides relevance, and is never modified or fabricated by dedup (F5).

### 3.6 Composition with exact dedup and the Hebbian floor

- **Exact-THOUGHT_ID dedup** (hook `seen_ids`, `auto_recall_hook.py:295-307`) stays as
  defense-in-depth; semantic dedup is upstream of it and cannot emit duplicate ids anyway
  (SQL rows are PK-unique, collapse only removes).
- **Hebbian floor (0.30)** gates the BOOST, not inclusion, and runs BEFORE dedup. Dedup
  therefore composes cleanly: it can only REMOVE atoms from the final ranked list, never
  add, re-score, or re-order. A below-floor atom that ranked low stays low or gets
  collapsed; dedup can never resurrect it or lift it above the floor's effect (D4).
- **Similarity threshold (`DEFAULT_SIMILARITY_THRESHOLD = 0.3`)** filtering is unchanged
  and runs before dedup; collapsed-atom ids listed in `NEAR_DUPLICATE_IDS` are always
  atoms that passed the threshold in THIS result set (relevant to R3 / VF_epsilon).

### 3.7 Dedup annotations surfaced in output

Survivor records gain two fields (absent, not null, when nothing collapsed):
- `NEAR_DUPLICATE_IDS: list[str]` - **FULL THOUGHT_IDs** of collapsed atoms, rank order.
  This deliberately refines the parent plan's "short_id" wording: `--inspect` resolves
  only full ids (`brain-{epoch}-{8hex}`), and the last-8 short-id is not a resolvable key,
  so short-ids in the machine field would break CCR reversibility (R3). Renderers
  (assembly line format Section 1.5, Pi T7b) DISPLAY the short form.
- `NEAR_DUPLICATE_COUNT: int`.

Text formatter (`_format_search_results`) appends ` (+N similar: <short-ids>)` on the
survivor's detail line when count > 0. Replay-log metadata gains additive keys
`"dedup": bool, "dedup_collapsed": int` (additive only; existing keys untouched).

### 3.8 Dedup invariants (gate for T3)

| ID | Invariant | Test predicate |
|---|---|---|
| D1 | Threshold collapse | 3 atoms pairwise cosine >= 0.92 -> 1 survivor with `NEAR_DUPLICATE_COUNT == 2` and both ids in `NEAR_DUPLICATE_IDS` |
| D2 | Missing-embedding fail-open | records without a parseable embedding are never collapsed and never absorb; mixed set -> no crash |
| D3 | Order preservation | survivor THOUGHT_ID sequence is a subsequence of the pre-dedup ranked sequence (no re-ordering, no promotion-above-floor side effects) |
| D4 | No resurrection | every id in output (survivor or NEAR_DUPLICATE_IDS) was present in the pre-dedup, post-threshold ranked set |
| D5 | Idempotence | applying the collapse pass to its own output changes nothing (`dedup(dedup(R)) == dedup(R)`) |
| D6 | No embedding egress | no returned dict (and no `--json` output) contains an embedding/`_EMBEDDING` key |
| D7 | Byte-stability without flag | `--search "x" --json` (no `--dedup`) output is byte-identical to pre-T3 behavior; `search(..., dedup=False)` executes the identical SQL |
| D8 | Distinct atoms kept | pairwise cosine 0.4 -> all atoms survive, counts absent |
| D9 | Reinforcement scope | `updated_at` is touched ONLY for returned survivors (within `limit`), not for collapsed or truncated atoms |

---

## 4. The CCR contract (reversible compression)

### 4.1 Principle

Summaries may be aggressive precisely because compression is REVERSIBLE: every atom is
ID-addressable, and `python3 open_brain.py --inspect <THOUGHT_ID>` expands it to full
content. Assembly therefore optimizes for scent (what + when + type + id), not
completeness.

### 4.2 Annotation format (the expand affordance)

- Every rendered atom line carries its 8-char short-id (Section 1.5) - the human scent.
- Every assembled block (recall block, and every T4 projected view) ends with exactly one
  hint line, the module constant `EXPAND_HINT`:
  `↳ expand any memory: python3 open_brain.py --inspect <id>`
- Machine surfaces carry FULL ids: `dropped_ids`, `annotations` keys,
  `NEAR_DUPLICATE_IDS`. Short-ids are display-only and are never accepted or designed as
  lookup keys (no short-id resolver; avoids ambiguity and a new query surface).

### 4.3 REVERSIBILITY invariant and the verified `--inspect` gap

**R1 (reversibility): no atom that influenced an assembled block is unreachable.** Every
atom that was included, summarized, budget-dropped, or dedup-collapsed is identified by a
full THOUGHT_ID somewhere in the assembly/search output, and `--inspect <THOUGHT_ID>`
(no time qualifier) returns its full `raw_text`.

**Verified gap that T3 MUST close for R1 to hold:** `time_travel.inspect_latest`
(`time_travel.py:192`) returns `None` when no `brain.thought_versions` rows exist, and
versions are only written by snapshot/update - so today, `--inspect <id>` on a plain
captured-and-never-snapshotted atom (the common case) prints "no version exists". Fix,
scoped to the same file/bead as T3: when `--inspect <id>` is invoked WITHOUT `--at` /
`--at-revision` and `inspect_latest` returns `None`, fall back to the live `brain.thoughts`
row (same `user_id` scoping predicate as `search()` - no scoping change), emitting the
same field set plus `"source": "live"` (versioned results emit `"source": "version"`).
`--at` / `--at-revision` semantics are UNCHANGED (`None` remains the correct answer for
"no version at that point" - documented behavior elsewhere depends on it).

### 4.4 CCR invariants (gate for T2 + T3)

| ID | Invariant | Test predicate |
|---|---|---|
| R1 | Round-trip | capture an atom -> `--search --dedup --json` -> take any THOUGHT_ID from results/`NEAR_DUPLICATE_IDS` -> `--inspect <id> --json` returns non-empty `raw_text` (integration test; exercises the 4.3 fallback) |
| R2 | Projection keeps the id | `project_fields(record, keep)` output contains the record's id key even when not listed in `keep` |
| R3 | Dedup ids resolvable | every element of `NEAR_DUPLICATE_IDS` is a full `brain-*` id (matches `^brain-\d+-[a-f0-9]{8}$`), not a short-id |
| R4 | Hint always present | `assemble_recall` with `included >= 1` -> `rendered` ends with `EXPAND_HINT`; every T4 projected text view ends with it |
| R5 | Summarization marked | any atom whose summary was truncated has `annotations[id]["summarized"] is True` (the agent can tell scent from full text) |

---

## 5. FIDELITY invariants preserved (constraints, not designs - nothing here is modified)

| Guarantee | How assembly/dedup preserves it |
|---|---|
| **PROV-DM provenance** | Read-path only: no writes to `brain.thoughts`, `atom_links`, `thought_versions`, or promotions. `SUPERSEDED_BY` / `DISPUTED` are computed by `_annotate_provenance` BEFORE dedup/assembly and pass through untouched; assembly RENDERS them as ` [superseded]` / ` [disputed]` flags (Section 1.5) and T4 keep-lists retain the raw fields - compression may shorten text but never strips a provenance warning. The 4.3 inspect fallback adds a read path with the identical user-scoping predicate. |
| **Trust envelope** | Assembly runs on the atom list BEFORE `build_recalled_memory_block` wraps it. `<recalled-memory-data>` tags, `_ENVELOPE_PREFACE`, and the injection-guard sanitizer are never touched by assembly; the hook sanitizes each assembled line via `sanitize_untrusted_string` exactly as today (T5 regression tests: envelope byte-exact, `test_envelope_tags_survive_assembly`). |
| **VF_epsilon (verified forgetting)** | Forgotten atoms have no live row, so they cannot enter `search()` results, `NEAR_DUPLICATE_IDS` (D4: only ids live in THIS result set), or assembly. R1 quantifies over atoms present at assembly time; a later forget legitimately makes an old block's id un-expandable - that is VF_epsilon working, not a CCR violation. No new persistence of atom content is introduced anywhere. |
| **PII redaction** | Redaction runs at the capture / replay-log emitter boundary, upstream of search. Assembly re-renders only text that `search()` already returns to the same caller today - no new egress class. The T3 replay-log additions are two booleans/ints (no new text fields), so the redaction pipeline's coverage is unchanged. |
| **NAL stv {f, c}** | `STV` and `LOW_CONFIDENCE` pass through unmodified; assembly surfaces ` [low-conf]` (threshold 0.35, computed upstream) and dedup uses `STV["c"]` ONLY as a tie-breaker for survivor selection (3.5). No stv value is ever computed, altered, averaged, or fabricated by assembly or dedup - NAL revision stays exclusively in `--revise`. |
| **Hebbian promotion** | Boost + floor (0.30) apply before dedup; assembly is order-preserving (B5), so a promotion's ranking effect survives the whole pipeline. Dedup never re-scores (D3/D4). |
| **Fail-open (hooks never crash)** | `count_tokens` fallback (B2); dedup skips embedding-less records (D2); T5 wraps `assemble_recall` so any exception falls back to the legacy top-5/200-char path (plan T5 `test_assembly_failure_falls_back_to_legacy`). Recall must NEVER block a user prompt. |

Fidelity gate tests (T2/T3/T5): **F1** assembled line for a DISPUTED atom contains
`[disputed]`; **F2** for a SUPERSEDED_BY atom contains `[superseded]`; **F3** for a
LOW_CONFIDENCE atom contains `[low-conf]`; **F4** `assemble_recall` output contains no key
and no rendered text derived from `_EMBEDDING`; **F5** `STV` values in survivor records
are bit-identical to their pre-dedup values.

---

## 6. Consolidated gate: interfaces + invariants (the checkable contract)

### 6.1 T2 gate - `scripts/recall_assembly.py`

Exact public surface (anything less fails the spec-review gate; supersets allowed):

```python
DEFAULT_RECALL_TOKEN_BUDGET: int = 600
SUMMARY_MIN_CHARS: int = 80
SHORT_ID_CHARS: int = 8
EXPAND_HINT: str  # "↳ expand any memory: python3 open_brain.py --inspect <id>"

def count_tokens(text: str) -> int
def summarize_to(text: str, max_chars: int) -> str
def project_fields(record: dict, keep: list[str]) -> dict
def assemble_recall(atoms: list[dict], token_budget: int = DEFAULT_RECALL_TOKEN_BUDGET) -> dict
```

Input: list of `search()`-shaped dicts (UPPERCASE keys, grounding table Section 0),
tolerating absent optional fields. Output: Section 1.4 shape. Module imports cleanly with
and without tiktoken installed; `import recall_assembly` performs no I/O.

Invariants: **B1-B6** (2.6), **R2, R4, R5** (4.4), **F1-F4** (Section 5), plus the plan's
nine named T2 tests (`test_count_tokens_fallback_without_tiktoken` ... 
`test_assemble_recall_empty_atoms_returns_empty`), which map onto B1/B2/B4/B5/R4 and the
`summarize_to` / `project_fields` behaviors in 2.4 / 1.2.

### 6.2 T3 gate - `open_brain.py` search dedup (+ inspect live-row fallback)

Exact surface:

```python
DEDUP_COSINE: float = 0.92
DEDUP_OVERFETCH_FACTOR: int = 3
DEDUP_OVERFETCH_CAP: int = 50

def search(conn, query, user_id, limit=DEFAULT_SEARCH_LIMIT,
           threshold=DEFAULT_SIMILARITY_THRESHOLD, sort_by="similarity",
           thought_type=None, topics=None, people=None,
           date_from=None, date_to=None, dedup: bool = False) -> List[Dict[str, Any]]
```

CLI: `--dedup` / `--no-dedup` (default OFF). Pi bridge `op:"search"` accepts optional
`"dedup"` (default false). Survivor fields `NEAR_DUPLICATE_IDS: list[str]` (full ids),
`NEAR_DUPLICATE_COUNT: int`. `--inspect <id>` without a time qualifier falls back to the
live row with `"source": "live"` (4.3).

Invariants: **D1-D9** (3.8), **R1, R3** (4.4), **F5** (Section 5), plus the plan's five
named T3 tests, which map onto D1/D8/D3/D2/D7.

### 6.3 Cross-cutting acceptance (T9/T11 verification hooks)

1. `assemble_recall(search_results, 600)["token_count"] <= 600` on a live corpus replay
   (T6 bench asserts B1 across all N prompts).
2. `--search "x" --json` byte-identical pre/post T3 when `--dedup` absent (D7).
3. Round-trip: id from a deduped search -> `--inspect` -> non-empty `raw_text` (R1).
4. Envelope invariant: assembled block wrapped byte-exact by the unchanged hook
   envelope (T5).
5. Dedup idempotence property test over synthetic embedding fixtures (D5).

---

*Design fits `open_brain.py` as of 2026-07-02 (search() at line 3302; constants at lines
263-264, 837, 2522-2524; provenance annotation at 3196; Pi bridge at 5421; inspect at
6465). T2/T3 implementers: read Section 0 first - every field name above is the real one.*
