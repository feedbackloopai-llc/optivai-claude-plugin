# Open Brain — Neurosymbolic Harness Port Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Every task in this plan corresponds to a bead in the local beads tracker. Beads close ONLY after the verification step. Anti-orphan-commit discipline is mandatory: every Sonnet implementer prompt MUST include the 7-step git workflow defined in §3.4.

**Goal:** Port the MS_ε mnemonic-sovereignty foundation (PV → RB → VF_ε → Hebbian) and three of the v0.20 magic-moment capabilities (CITATION → INSPECT → REPLAY-LOG) from `optivai-builder`'s TypeScript neurosymbolic harness into the Open Brain Python stack consumed by `optivai-claude-plugin` and `optivai-pi-plugin`. Ship as `brain-v0.2.0-neurosymbolic`.

**Architecture:** `optivai-claude-plugin/scripts/open_brain.py` (1,537 LOC today) is the single canonical Python module; Pi plugin consumes via skills that shell out to the installed copy at `~/.claude/hooks/open_brain.py`. Schema lives at `optivai-claude-plugin/sql/BRAIN_SCHEMA_PG.sql`. All Wave-1+2 work is single-repo (claude-plugin); Pi plugin gets only skill-doc updates in Wave-2 Phase D. Dependency pre-order from Lin/Li/Chen 2026 §12.1 (`VF_ε ⪯ RB ⪯ PV ⪯ WA`) forces task order — Wave-1 lands PV first as the foundational substrate. Beads track the entire DAG.

**Tech Stack:** Python 3.11+ · `psycopg2-binary` · `pgvector` · `sentence-transformers/all-mpnet-base-v2` · `anthropic` · `jsonpatch` (RFC 6902 — Python equivalent of fast-json-patch) · `pytest` · Neon PostgreSQL + pgvector · Steve Yegge `beads` CLI · git pre-commit hooks already in repo (P16-S4 sync-conflict guard).

---

## 1. Pre-flight: branch + bead DAG

### 1.1 Cut the working branch

```bash
cd /Users/erato949/Documents/optivai-claude-plugin
git checkout main && git pull --rebase
git checkout -b brain-v0.2.0-neurosymbolic
git status   # must be clean
```

### 1.2 Create the bead graph (Wave-1 + Wave-2 + reviews + releases)

Run each `beads create` once, then wire dependencies with `beads depend`. The full DAG is documented in §6 (visual). All beads use priority `1` (P1 release-blocking) unless noted.

```bash
# Wave-1 — MS_ε foundation
beads create "brain-W1-S0: branch + scaffolding + bead-DAG verify"           -t task    -p 1
beads create "brain-W1-S1: PROV-DM schema migration on brain.thoughts"       -t feature -p 1
beads create "brain-W1-S2: PROV-DM capture flow (open_brain.py + brain_hook)" -t feature -p 1
beads create "brain-W1-S3: PROV-DM test corpus"                              -t task    -p 1
beads create "brain-W1-S4: brain.thought_versions table (RB schema)"         -t feature -p 1
beads create "brain-W1-S5: RB CLI (--snapshot --versions --rollback --diff)" -t feature -p 1
beads create "brain-W1-S6: RB test corpus"                                   -t task    -p 1
beads create "brain-W1-S7: VF_eps probe library (Hoeffding + exact binomial)" -t feature -p 1
beads create "brain-W1-S8: --forget CLI with delete-after-verify + audit"    -t feature -p 1
beads create "brain-W1-S9: VF_eps test corpus + benign-persistence cases"    -t task    -p 1
beads create "brain-W1-S10: brain.promotions table (Hebbian)"                -t feature -p 1
beads create "brain-W1-S11: --promote/--demote CLI + retrieval scoring"      -t feature -p 1
beads create "brain-W1-S12: Hebbian test corpus + time-decay math"           -t task    -p 1
beads create "brain-W1-R0: Wave-1 dual-pass review (Opus sec + Sonnet CQ)"   -t task    -p 1
beads create "brain-W1-RELEASE: tag brain-v0.2.0-neurosymbolic-alpha"        -t task    -p 1

# Wave-2 — magic moments
beads create "brain-W2-S1: citation walker (--trace) port from citation-walker.ts" -t feature -p 1
beads create "brain-W2-S2: citation walker test corpus"                            -t task    -p 1
beads create "brain-W2-S3: inspect-memory (--inspect --at) port from time-travel.ts" -t feature -p 1
beads create "brain-W2-S4: inspect-memory test corpus"                             -t task    -p 1
beads create "brain-W2-S5: brain.replay_log schema (PII-distinct + OTel trace_id)" -t feature -p 1
beads create "brain-W2-S6: replay log capture instrumentation"                     -t feature -p 1
beads create "brain-W2-S7: --replay CLI + session reconstruction"                  -t feature -p 1
beads create "brain-W2-S8: replay log test corpus"                                 -t task    -p 1
beads create "brain-W2-D1: Pi plugin skill-doc sync (brain-* SKILL.md updates)"    -t task    -p 2
beads create "brain-W2-R0: Wave-2 dual-pass review"                                -t task    -p 1
beads create "brain-W2-RELEASE: tag brain-v0.2.0-neurosymbolic-beta"               -t task    -p 1
```

After every `beads create` returns its ID, capture them into shell variables for the dependency-wiring pass:

```bash
# Re-list and grab IDs into a shell associative array; humans paste these
beads list --status open | grep -E "brain-W[12]-" > /tmp/brain-beads-ids.txt
cat /tmp/brain-beads-ids.txt
```

### 1.3 Wire the dependency graph

Replace `<S0>`, `<S1>`, etc. with the actual `gz-xxxxx` IDs from §1.2. Run these in order — beads validates and rejects cycles.

```bash
# Wave-1 chain: S0 -> {S1, S10}; S1 -> S2 -> S3; S1 -> S4 -> S5 -> S6;
#               S2 -> S7 -> S8 -> S9; S10 -> S11 -> S12; {S3,S6,S9,S12} -> R0 -> RELEASE
beads depend <S1>  <S0>
beads depend <S10> <S0>
beads depend <S2>  <S1>
beads depend <S3>  <S2>
beads depend <S4>  <S1>
beads depend <S5>  <S4>
beads depend <S6>  <S5>
beads depend <S7>  <S2>
beads depend <S8>  <S7>
beads depend <S9>  <S8>
beads depend <S11> <S10>
beads depend <S12> <S11>
beads depend <W1R0> <S3>
beads depend <W1R0> <S6>
beads depend <W1R0> <S9>
beads depend <W1R0> <S12>
beads depend <W1REL> <W1R0>

# Wave-2 chain: RELEASE-alpha -> {W2S1, W2S3, W2S5}; W2S5 -> W2S6 -> W2S7 -> W2S8;
#               W2S1 -> W2S2; W2S3 -> W2S4; {S2,S4,S8} -> R0; R0 -> D1 -> RELEASE-beta
beads depend <W2S1> <W1REL>
beads depend <W2S3> <W1REL>
beads depend <W2S5> <W1REL>
beads depend <W2S2> <W2S1>
beads depend <W2S4> <W2S3>
beads depend <W2S6> <W2S5>
beads depend <W2S7> <W2S6>
beads depend <W2S8> <W2S7>
beads depend <W2R0> <W2S2>
beads depend <W2R0> <W2S4>
beads depend <W2R0> <W2S8>
beads depend <W2D1> <W2R0>
beads depend <W2REL> <W2D1>

# Verify the graph
beads ready  # Should show ONLY brain-W1-S0 as ready (everything else blocked)
```

### 1.4 Acceptance gate for §1

- `git status` clean on `brain-v0.2.0-neurosymbolic`
- `beads ready` returns exactly **1** bead (`brain-W1-S0`)
- `beads list --status open | grep brain-W | wc -l` returns **26**
- `cat /tmp/brain-beads-ids.txt` exists and contains 26 IDs
- Commit the bead-id mapping to the branch:
  ```bash
  cp /tmp/brain-beads-ids.txt docs/plans/2026-05-21-brain-bead-ids.txt
  git add docs/plans/2026-05-21-brain-bead-ids.txt
  git commit -m "chore(brain-v0.2): wire Wave-1+Wave-2 bead DAG (26 beads)"
  ```
- `beads close <S0>` — S0 is "branch + DAG verified" and this commit IS the verification.

---

## 2. Tiered model dispatch — agent assignment per task class

**The rule:** Opus 4.7 thinks; Sonnet 4.6 writes code; Haiku 4.5 writes docs and trivial bumps. Never invert.

| Task class | Agent | Model | Why |
|---|---|---|---|
| Architecture spec for any new primitive | `solution-architect-planner` | Opus 4.7 | Spec quality determines impl quality |
| TDD implementation of any S-task | `implementation-developer` | Sonnet 4.6 | Production-coder; never `implementer` (sandboxed — see [[session-2026-05-20-21-wave3-wave4-marathon]] §execution-discipline) |
| Code-quality review gate | `code-quality-reviewer` | Opus 4.7 | Catches mock-vs-prod divergence + structural bugs |
| Security review (R0 dual-pass) | `claude` (general-purpose, Opus) with security focus | Opus 4.7 | MS_ε is a security primitive — Opus eyes only |
| Test corpus generation | `implementation-developer` | Sonnet 4.6 | Test code is still code |
| Docs / SKILL.md / partner-setup edits | `technical-writer` | Sonnet 4.6 | Prose at minimum cost |
| Bumping version strings, README counters | inline Bash | Haiku 4.5 (or no agent) | No reasoning needed |
| Bead state transitions | inline Bash | none | `beads update`/`close` is one command |

**Orchestrator (me, Opus 4.7) responsibilities:**
- Dispatch architect for spec → review spec → dispatch implementer with full file paths + anti-orphan workflow embedded → run review → close bead → check next ready
- NEVER write impl code in the orchestrator session — delegate to Sonnet implementer
- NEVER skip the spec step for the first task in a phase — pattern recurrence within a phase can skip the spec
- For the 8 S-tasks that touch `open_brain.py`, dispatch ONE implementer per task (not batched) — file shared, collision risk per [[session-2026-05-20-21-wave3-wave4-marathon]]

---

## 3. Anti-orphan-commit + TDD discipline (mandatory)

### 3.1 Every implementer prompt MUST include this verbatim block

```text
MANDATORY WORKFLOW (deviation = bead does not close):

1. Pre-flight: `cd /Users/erato949/Documents/optivai-claude-plugin && git status`
   — confirm clean tree on branch brain-v0.2.0-neurosymbolic. If dirty, STOP and report.

2. Write the failing test FIRST (TDD). Run it. Confirm it fails with the expected
   error (not a syntax error, not an import error — the SEMANTIC failure that
   says "the feature does not exist yet").

3. Implement the MINIMAL code that makes the test pass. No bonus features.
   No bonus MCP tools. No bonus CLI flags. Stay within the bead scope.

4. Run the tests. Confirm GREEN.

5. Run `python3 -m pytest tests/ -x` (full suite). Baseline preservation required:
   the count of failing tests must NOT increase. Document any baseline drift in
   your report.

6. `git status` — verify NO untracked files of YOUR creation (`??` lines).
   If any exist, either add them or report why they are intentional.

7. `git add <files>` — explicit paths only, never `git add -A` or `git add .`
   (pre-commit hook may reject; see CLAUDE.md P16-S4).

8. `git commit -m "feat(brain-W{N}-S{M}): <one-line summary> (<bead-id>)"`
   — bead ID in commit message is required for traceability.

9. `git log --oneline -1` — verify the commit landed at HEAD.

10. Report:
    - Files created/modified
    - Test command + count of new passing tests
    - Baseline drift (if any)
    - Commit SHA
    - Bead ID

11. DO NOT call `beads close`. The orchestrator runs the code-review gate
    and closes the bead only after YELLOW-or-better verdict.

If ANY step fails, STOP and report the failure with full output. The orchestrator
will decide whether to fix-forward or revert.
```

### 3.2 Every architect prompt MUST include this verbatim block

```text
SPEC OUTPUT REQUIREMENTS:

- 400-800 line markdown spec at docs/specs/brain-W{N}-S{M}-spec.md
- Exact file paths to create/modify (with line ranges for modifications)
- Exact function signatures with type hints
- Exact SQL DDL where applicable (CREATE TABLE / ALTER TABLE — additive only;
  no DROP, no destructive migrations — see CLAUDE.md "NO DESTRUCTIVE DATABASE
  COMMANDS")
- Test contract: what behaviors the test corpus must cover, with concrete
  example inputs and expected outputs (3+ examples per behavior)
- Reference: the equivalent file in optivai-builder (e.g.,
  src/agents/atomspace.ts:NNN-NNN) — read it before drafting, cite line numbers
- Anti-patterns to avoid (e.g., for VF_eps: DO NOT delete before verify —
  see R0 review finding R1 from optivai-v0.19)
- Acceptance criteria: enumerated bullet list the implementer can self-check

You read files independently — do not require me to paste content. Cite
file_path:line_number for every reference.
```

### 3.3 Every code-review gate prompt MUST include

```text
REVIEW GATE — verdict required:

Read the bead's commit diff and the files it touched. Produce one of:

- GREEN: ship as-is, no findings
- YELLOW: ship + file follow-up beads for non-blocking findings (list each)
- RED: do NOT ship. List blockers. Orchestrator dispatches fix-wave.

For MS_eps primitives (PV/RB/VF/Hebbian) emphasize:
- Mock-vs-prod divergence (see [[pattern_mock_vs_prod_divergence]] — UUID/TEXT
  column types, ESM vs CJS imports). Production-blocker class.
- Dependency pre-order: does this primitive correctly require the lower
  primitives to be operational? (PV before RB; RB before VF_eps)
- Audit log emission: does every governance operation emit a structured
  audit entry with all required fields?
- Anti-orphan: are any new test/* files uncommitted?

For magic moments (CITATION/INSPECT/REPLAY):
- Provenance chain correctness (no orphan derivedFrom edges)
- Time-travel snapshot fidelity (revision counter monotonic)
- PII redaction completeness in replay log (no leakage in audit fields)

Report under 400 words. List file_path:line_number for every finding.
```

### 3.4 Branch hygiene at every wave boundary

Before dispatching the R0 review for a wave:
```bash
git log --oneline brain-v0.2.0-neurosymbolic ^main | wc -l   # count wave commits
git status                                                    # confirm clean
beads list --status open | grep -E "brain-W{N}-S" | wc -l    # should be 0 open S-tasks
```

If any S-task bead is still open at the wave boundary, that's a process failure — the orchestrator should NOT have dispatched the review.

---

## 4. Wave-1 — MS_ε Foundation

### Task brain-W1-S1: PROV-DM schema migration

**Why this is the foundation:** Lin/Li/Chen 2026 §12.1 dependency pre-order: a system that doesn't tag provenance at write CANNOT support meaningful rollback or verified forgetting. PV is the structural prerequisite. Today `brain.thoughts` has a `source` VARCHAR(50) which is one bit of provenance; we need the full PROV-DM record.

**Reference:** `optivai-builder/src/agents/atomspace.ts:42-95` (the `AtomMetadata.prov` interface).

**Files:**
- Modify: `sql/BRAIN_SCHEMA_PG.sql:10-26` (add columns + non-null constraint plan)
- Create: `sql/migrations/2026-05-21-prov-dm.sql` (one-shot ALTER for existing installs)
- Test: `tests/test_prov_dm_schema.py`

**Step 1 — Architect (Opus, solution-architect-planner)**

Dispatch with spec requirements from §3.2. The architect MUST output `docs/specs/brain-W1-S1-spec.md` covering:
- The 5 PROV-DM fields per W3C PROV-DM 1.3 specification: `prov_agent` (who did the write — agent ID), `prov_activity` (what operation — `capture/import/sync/forget-residue`), `was_generated_by` (parent activity ID), `was_derived_from` (parent thought_id if derivative, NULL for original), `source_uri` (canonical URI of the source if external)
- Whether to use JSONB single-column `prov` or 5 discrete columns (cite tradeoffs)
- Backfill plan for existing `brain.thoughts` rows (must be non-destructive — assign defaults like `prov_agent='legacy-import'`, `prov_activity='unknown'`)
- Migration ordering: ADD COLUMN nullable → backfill → ADD CONSTRAINT NOT NULL (3-step, never single-shot)

**Step 2 — Write failing test (Sonnet, implementation-developer)**

Test contract (architect spec drives this):
```python
# tests/test_prov_dm_schema.py
import psycopg2
import os

def test_thoughts_table_has_prov_columns(conn):
    """PV primitive: every thought must have queryable provenance."""
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, is_nullable, data_type
        FROM information_schema.columns
        WHERE table_schema='brain' AND table_name='thoughts'
          AND column_name IN ('prov_agent','prov_activity','was_generated_by',
                              'was_derived_from','source_uri')
        ORDER BY column_name
    """)
    rows = cur.fetchall()
    assert len(rows) == 5, f"Expected 5 PROV columns, got {len(rows)}: {rows}"
    # After backfill + constraint, all required PROV fields must be NOT NULL
    # except was_derived_from (NULL for originals) and source_uri (NULL for internal)
    required_not_null = {'prov_agent', 'prov_activity', 'was_generated_by'}
    for col_name, is_nullable, _ in rows:
        if col_name in required_not_null:
            assert is_nullable == 'NO', f"{col_name} must be NOT NULL after migration"

def test_legacy_rows_have_backfilled_prov(conn):
    """Existing rows pre-migration get prov_agent='legacy-import' default."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM brain.thoughts
        WHERE prov_agent IS NULL OR prov_activity IS NULL
    """)
    assert cur.fetchone()[0] == 0, "All rows must have prov_agent + prov_activity"
```

**Step 3 — Run test, confirm fail**

```bash
python3 -m pytest tests/test_prov_dm_schema.py -v
# Expected: FAIL (columns do not exist yet)
```

**Step 4 — Write the migration**

Implementer writes `sql/migrations/2026-05-21-prov-dm.sql`:

```sql
-- Brain v0.2.0-neurosymbolic — PROV-DM PV primitive
-- W3C PROV-DM 1.3 conformant: agent + activity + wasGeneratedBy + wasDerivedFrom + sourceUri
-- Non-destructive 3-step: ADD nullable → backfill → ADD CONSTRAINT NOT NULL
BEGIN;

-- Step 1: Add columns (all nullable initially)
ALTER TABLE brain.thoughts
  ADD COLUMN IF NOT EXISTS prov_agent       VARCHAR(100),
  ADD COLUMN IF NOT EXISTS prov_activity    VARCHAR(50),
  ADD COLUMN IF NOT EXISTS was_generated_by VARCHAR(64),
  ADD COLUMN IF NOT EXISTS was_derived_from VARCHAR(64),  -- references thoughts.thought_id; NULL OK
  ADD COLUMN IF NOT EXISTS source_uri       TEXT;          -- NULL for internal

-- Step 2: Backfill legacy rows (idempotent)
UPDATE brain.thoughts
SET prov_agent     = COALESCE(prov_agent, 'legacy-import'),
    prov_activity  = COALESCE(prov_activity, 'unknown'),
    was_generated_by = COALESCE(was_generated_by, 'activity-legacy-' || thought_id)
WHERE prov_agent IS NULL
   OR prov_activity IS NULL
   OR was_generated_by IS NULL;

-- Step 3: Enforce NOT NULL on required PROV fields
ALTER TABLE brain.thoughts
  ALTER COLUMN prov_agent       SET NOT NULL,
  ALTER COLUMN prov_activity    SET NOT NULL,
  ALTER COLUMN was_generated_by SET NOT NULL;

-- Step 4: Add FK constraint for derivative chain (soft — allow NULL, enforce existence when set)
ALTER TABLE brain.thoughts
  ADD CONSTRAINT IF NOT EXISTS fk_thoughts_derived_from
  FOREIGN KEY (was_derived_from) REFERENCES brain.thoughts(thought_id)
  ON DELETE SET NULL  -- if parent gets forgotten, mark child as orphan rather than cascade
  DEFERRABLE INITIALLY DEFERRED;

-- Step 5: Index for citation walker (Wave-2 will use this)
CREATE INDEX IF NOT EXISTS idx_thoughts_derived_from
  ON brain.thoughts(was_derived_from) WHERE was_derived_from IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_thoughts_generated_by
  ON brain.thoughts(was_generated_by);

COMMIT;
```

And updates the canonical `sql/BRAIN_SCHEMA_PG.sql` to include the same columns in the `CREATE TABLE IF NOT EXISTS` block so fresh installs land with PROV-DM from line 1.

**Step 5 — Run test, confirm pass**

```bash
# Migration runner is python3 scripts/open_brain.py --migrate <path>
# If it doesn't exist yet, the implementer creates it as a 30-line helper.
python3 scripts/open_brain.py --migrate sql/migrations/2026-05-21-prov-dm.sql
python3 -m pytest tests/test_prov_dm_schema.py -v
# Expected: PASS (2 tests)
```

**Step 6 — Full-suite baseline check**

```bash
python3 -m pytest tests/ -x
# Expected: baseline-or-better. Document any drift.
```

**Step 7 — Commit per §3.1 workflow**

```bash
git add sql/BRAIN_SCHEMA_PG.sql sql/migrations/2026-05-21-prov-dm.sql \
        tests/test_prov_dm_schema.py scripts/open_brain.py \
        docs/specs/brain-W1-S1-spec.md
git commit -m "feat(brain-W1-S1): PROV-DM schema migration on brain.thoughts (<bead-id>)"
```

**Step 8 — Review gate (Opus, code-quality-reviewer)**

Dispatch per §3.3. Expected verdict: GREEN or YELLOW.

**Step 9 — Close bead**

```bash
beads update <S1> --status in_progress  # (skip if already in_progress)
beads close <S1>
beads ready  # should now show brain-W1-S2 and brain-W1-S4 as ready
```

---

### Task brain-W1-S2: PROV-DM capture flow

**Goal:** Every `--capture` writes a complete PROV-DM record. Hooks that auto-capture (brain_hook.py) get a default `prov_agent` derived from the caller (claude-code | pi | manual).

**Reference:** `optivai-builder/src/agents/atomspace.ts:148-220` (addAtom validation).

**Files:**
- Modify: `scripts/open_brain.py` — function `capture_thought()` (find it via `grep -n "def capture_thought\|def _capture" scripts/open_brain.py`)
- Modify: `scripts/hooks/brain_hook.py` (find via `find scripts -name "brain_hook*"`)
- Modify: `scripts/open_brain.py` argparse — add `--prov-agent`, `--prov-activity`, `--derived-from` flags (all optional with sensible defaults)
- Test: `tests/test_prov_dm_capture.py`

**Step 1 — Architect spec** (per §3.2)

The architect MUST decide:
- Default `prov_agent` derivation: `manual` → `cli-user-{USER}`; `--from-pi` → `pi-agent`; brain_hook auto-capture → `claude-code-hook-{operation}`
- Default `prov_activity`: `--capture` → `capture`; auto-capture → `auto-capture-{trigger}` (e.g., `auto-capture-decision-signal`)
- `was_generated_by`: generate a stable activity UUID (`activity-{thought_id}` works — 1:1 with thought initially; richer multi-thought activities deferred to Wave-3 atomspace)
- Validation: reject capture if `was_derived_from` references a thought_id that doesn't exist in this user's scope (PS scoping check)

**Step 2 — Failing test**

```python
# tests/test_prov_dm_capture.py
def test_capture_populates_prov_dm(monkeypatch, conn):
    """Every CLI capture writes complete PROV-DM."""
    from scripts.open_brain import capture_thought
    tid = capture_thought(
        text="Decision: ship brain-v0.2.0",
        source="manual",
        prov_agent=None,  # let it default
        prov_activity=None,
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT prov_agent, prov_activity, was_generated_by, was_derived_from
        FROM brain.thoughts WHERE thought_id = %s
    """, (tid,))
    row = cur.fetchone()
    assert row[0].startswith("cli-user-"), f"prov_agent={row[0]}"
    assert row[1] == "capture"
    assert row[2] is not None and row[2].startswith("activity-")
    assert row[3] is None  # original, not derivative

def test_capture_rejects_invalid_derived_from(conn):
    """PS scoping: was_derived_from must reference an existing thought in scope."""
    from scripts.open_brain import capture_thought, BrainError
    with pytest.raises(BrainError, match="was_derived_from .* not found"):
        capture_thought(text="...", was_derived_from="nonexistent-id-xyz")

def test_hook_auto_capture_uses_distinct_prov_agent(monkeypatch):
    """brain_hook.py auto-captures get prov_agent='claude-code-hook-*'."""
    # ... fixture sets up subprocess that invokes brain_hook on a stdin event
```

**Step 3-9** — same TDD + commit + review + close pattern as S1.

---

### Task brain-W1-S3: PROV-DM test corpus

**Goal:** 15+ tests covering: original capture, derivative capture, PS scoping rejection, FK constraint on derived_from, backfill idempotency, multi-user isolation of provenance, source_uri optional, prov_activity vocabulary enforcement.

This is pure test code — no new feature surface. Implementer Sonnet, no architect needed (S2 spec already defines the contract).

**Acceptance:** `pytest tests/test_prov_dm*.py -v` passes 15+ tests.

---

### Task brain-W1-S4: brain.thought_versions table (RB schema)

**Why now:** PV must exist first (S1) — every version row references the PROV chain of its parent. RB depends on PV per §12.1 dependency pre-order.

**Reference:** `optivai-builder/src/agents/atomspace.ts:340-490` (snapshot/getVersions/rollback/diff); the `atom_versions` table schema is in the `CREATE TABLE IF NOT EXISTS` block of that file.

**Files:**
- Modify: `sql/BRAIN_SCHEMA_PG.sql` (add `brain.thought_versions` block)
- Create: `sql/migrations/2026-05-21-rb-versions.sql`
- Test: `tests/test_rb_schema.py`

**Schema (architect drafts, implementer codes from spec):**

```sql
CREATE TABLE IF NOT EXISTS brain.thought_versions (
    version_id      BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES brain.thoughts(thought_id) ON DELETE CASCADE,
    revision        INTEGER           NOT NULL,             -- monotonic per thought_id starting at 1
    raw_text        TEXT              NOT NULL,
    summary         VARCHAR(1000),
    thought_type    VARCHAR(50),
    topics          JSONB,
    people          JSONB,
    action_items    JSONB,
    embedding       vector(768),
    metadata        JSONB,
    -- PROV-DM at the version level (every snapshot is its own PROV event)
    prov_agent      VARCHAR(100)      NOT NULL,
    prov_activity   VARCHAR(50)       NOT NULL,             -- 'snapshot' | 'rollback' | 'update'
    parent_version  BIGINT            REFERENCES brain.thought_versions(version_id),  -- prior version
    diff_json       JSONB,                                   -- RFC 6902 patch from parent_version → this
    created_at      TIMESTAMPTZ       DEFAULT NOW(),
    UNIQUE (thought_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_thought_versions_thought ON brain.thought_versions (thought_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_thought_versions_created ON brain.thought_versions (created_at DESC);
```

**Critical invariant the test corpus enforces:** `revision` is monotonic per `thought_id` (you cannot insert a row at revision 3 if revisions 1 and 2 don't exist).

---

### Task brain-W1-S5: RB CLI (`--snapshot --versions --rollback --diff`)

**Reference:** `atomspace.ts:520-680` (snapshot, getVersions, rollback, diff methods).

**Files:**
- Modify: `scripts/open_brain.py` (add 4 functions + 4 argparse subcommands)
- Test: `tests/test_rb_cli.py`

**Critical detail — rollback creates new history, does NOT rewrite:**

This is the Lin/Li/Chen §12.1 RB primitive contract. A rollback to revision N appends revision N+M (where M is the next-available revision number) with `prov_activity='rollback'` and `parent_version=<version_id of N>`. The intermediate revisions (N+1 .. M-1) remain in history. This is the same pattern optivai-builder uses (atomspace.ts:621 — "rollback creates new history not rewrite"). The R0 review of v0.19 specifically called out this requirement.

**Failing test for the critical invariant:**

```python
def test_rollback_creates_new_history_not_rewrite(conn):
    tid = capture_thought("v1 text")            # revision 1
    update_thought(tid, raw_text="v2 text")     # revision 2
    update_thought(tid, raw_text="v3 text")     # revision 3
    rollback_thought(tid, to_revision=1)        # creates revision 4 == content of revision 1

    versions = list_versions(tid)
    assert len(versions) == 4, f"Expected 4 rows in history, got {len(versions)}"
    assert versions[-1]['revision'] == 4
    assert versions[-1]['raw_text'] == "v1 text"
    assert versions[-1]['prov_activity'] == "rollback"
    assert versions[-1]['parent_version'] == versions[0]['version_id']
    # Revisions 2 and 3 still exist — rollback does NOT delete them
    assert versions[1]['raw_text'] == "v2 text"
    assert versions[2]['raw_text'] == "v3 text"
```

**`--diff` implementation note:** uses `jsonpatch` library (PyPI: `jsonpatch`, RFC 6902) — Python equivalent of fast-json-patch. The diff is computed across the full thought record minus the embedding vector (vectors don't diff usefully; embed an "embedding_changed: true" sentinel if cosine distance > 0.05).

---

### Task brain-W1-S6: RB test corpus

10+ tests: monotonic revision FK, rollback-creates-new-history invariant, diff format conformance to RFC 6902, snapshot idempotency, cross-user version isolation, version_id stability across rollback.

---

### Task brain-W1-S7: VF_ε probe library

**Why now:** VF_ε is the apex of the MS_ε dependency pre-order — requires RB (S4-S6) operational because the delete-after-verify pattern relies on snapshotting the about-to-be-forgotten state BEFORE the verification probes run. Without RB, you couldn't restore on probe failure.

**Reference:** `optivai-builder/src/agents/vf-probe.ts` (633 LOC). This file is the most security-critical port — read end-to-end before drafting the spec.

**Files:**
- Create: `scripts/vf_probe.py` (~300-400 LOC — Python port of vf-probe.ts; drops embedding-NN heavy logic in favor of simpler probe strategies for first cut)
- Test: `tests/test_vf_probe.py`

**Step 1 — Architect spec (Opus, ~1000 line spec is appropriate here)**

The spec MUST cover:
- **Probe budget:** n=300 (matches optivai-builder; gives both bounds at procurement-defensible values)
- **Distribution at first cut:** 120 semantic-neighbor / 90 paraphrase / 60 partial-fragment / 30 embedding-perturb (mirrors 40/30/20/10 ratio)
- **Dual-bound calculation:**
  - Hoeffding one-sided: `exp(-2 * n * eps**2)` = `exp(-2*300*0.05^2)` = **0.2231** → 77.69% confidence (loose, named in audit as such)
  - Exact binomial (k=0): `(1 - eps)^n` = `0.95^300` ≈ **2.075e-7** → **99.9999793%** confidence (tight; this is the procurement claim)
  - Audit log MUST carry both as labeled distinct fields: `hoeffdingBound`, `hoeffdingConfidence`, `exactBinomialBound`, `exactBinomialConfidence`, `probeQuality`
- **Probe quality marker:** `{n: 300, distribution: {semantic: 120, paraphrase: 90, partial: 60, perturb: 30}, sampledFromSnapshot: true}`
- **Delete-after-verify pattern (R0 v0.19 R1 blocker — non-negotiable):**
  ```
  1. snapshot the forgotten thought_id + all its versions  → ProbeSeedSnapshot
  2. generate 300 probes from the snapshot (not from the live store)
  3. DELETE the thought (and its versions; FK CASCADE handles it)
  4. for each probe: search the live store, score whether the snapshot content
     would surface in top-K results. accept iff k=0 (zero residue).
  5. emit audit log entry with both bounds
  6. if any probe fires (k>0): restore from snapshot, mark as `forget-failed`,
     emit a different audit event
  ```
- **Anti-patterns to forbid** (lifted from optivai-v0.19 R0 review):
  - DO NOT delete before verify (that's the bug that R0 R1 blocked)
  - DO NOT use Hoeffding alone in the audit (R2 fix wave)
  - DO NOT trust live-store probes (R3 — must snapshot first)

**Step 2 — Failing test (just one to start, then the corpus task S9 builds out 30+)**

```python
def test_vf_eps_audit_records_both_bounds(conn, capsys):
    from scripts.vf_probe import forget_with_verification
    tid = capture_thought("Embarrassing thing I want forgotten")
    result = forget_with_verification(tid, epsilon=0.05, n=300)
    assert result.status == "forgotten"
    assert result.audit['hoeffdingBound'] == pytest.approx(0.2231, abs=1e-3)
    assert result.audit['hoeffdingConfidence'] == pytest.approx(0.7769, abs=1e-3)
    assert result.audit['exactBinomialBound'] == pytest.approx(2.075e-7, abs=1e-8)
    assert result.audit['exactBinomialConfidence'] == pytest.approx(0.999_999_79, abs=1e-6)
    assert result.audit['probeQuality']['n'] == 300
```

**Step 3 — Implement `scripts/vf_probe.py`**

Implementer ports vf-probe.ts. Estimated 4-6 hours for Sonnet given the spec.

**Step 4-9** — TDD + commit + review + close.

---

### Task brain-W1-S8: `--forget` CLI

**Files:**
- Modify: `scripts/open_brain.py` (add `--forget <id>` subcommand)
- Add: `brain.forget_audit` table (architect decides if separate from generic `landing.raw_events` or a dedicated table — recommendation: dedicated, for procurement-grade auditability)
- Test: `tests/test_forget_cli.py`

**One acceptance criterion that defines this bead:**

```bash
$ python3 scripts/open_brain.py --capture "secret thing"
brain-1779999999-abcd1234

$ python3 scripts/open_brain.py --forget brain-1779999999-abcd1234 --epsilon 0.05 --json
{
  "thought_id": "brain-1779999999-abcd1234",
  "status": "forgotten",
  "audit": {
    "n": 300,
    "k": 0,
    "epsilon": 0.05,
    "hoeffdingBound": 0.2231,
    "hoeffdingConfidence": 0.7769,
    "exactBinomialBound": 2.075e-7,
    "exactBinomialConfidence": 0.99999979,
    "probeQuality": {"n": 300, "distribution": {"semantic": 120, "paraphrase": 90, "partial": 60, "perturb": 30}, "sampledFromSnapshot": true},
    "snapshotId": "snap-1779999999-xyz",
    "forgottenAt": "2026-05-21T19:30:00Z",
    "prov_agent": "cli-user-erato949"
  }
}

$ python3 scripts/open_brain.py --search "secret thing" --json
[]   # zero residue
```

---

### Task brain-W1-S9: VF_ε test corpus + benign-persistence cases

30+ tests covering:
- Both bound calculations (Hoeffding + exact binomial) at multiple n values
- Delete-after-verify ordering (delete must not happen before snapshot)
- Restore-on-probe-failure pattern
- Benign-persistence cases (Lin/Li/Chen §12.2) — UCC-style cross-user contamination probes
- Audit log completeness (every field present, types correct)
- Probe quality marker correctness
- Verification REJECTS forget when k > 0

---

### Task brain-W1-S10: brain.promotions table (Hebbian)

**Reference:** `optivai-builder/src/extension/tools/memory-tools.ts:promotion logic` + `memory_promotions` table from atomspace.ts.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS brain.promotions (
    promotion_id    BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES brain.thoughts(thought_id) ON DELETE CASCADE,
    user_id         VARCHAR(100)      NOT NULL,
    weight          DOUBLE PRECISION  NOT NULL DEFAULT 1.0,
    promoted_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    prov_agent      VARCHAR(100)      NOT NULL,    -- who promoted (agent or user)
    reason          TEXT,                          -- optional human-readable rationale
    UNIQUE (thought_id, user_id, promoted_at)
);

CREATE INDEX IF NOT EXISTS idx_promotions_thought_user ON brain.promotions (thought_id, user_id);
```

**Time-decay formula** (applied at retrieval time, not stored): `effective_weight = sum(weight * (1 + days_since_promoted_at)^(-0.7))` over all rows for `(thought_id, user_id)`.

---

### Task brain-W1-S11: `--promote/--demote` CLI + retrieval scoring integration

**Files:**
- Modify: `scripts/open_brain.py` — add `--promote <id> [--weight N] [--reason "..."]`, `--demote <id>`, integrate `effective_weight` into the `HYBRID_SCORE` calculation
- Test: `tests/test_hebbian_cli.py`

**Critical defense — within-kind over-application** (per [[gz-dsax2]] from optivai-builder P21 backlog):

The promotion weight must be gated by a minimum semantic similarity floor for the current query. Without this, a heavily-promoted but irrelevant thought ranks above a low-promoted but highly-relevant one. The architect spec must include a `MIN_RELEVANCE_FOR_PROMOTION_FLOOR = 0.30` constant and the test corpus must cover the gated case.

---

### Task brain-W1-S12: Hebbian test corpus

10+ tests: monotone weight accumulation, time-decay math correctness, within-kind over-application gate, demote-below-zero behavior, multi-user promotion isolation.

---

### Task brain-W1-R0: Wave-1 dual-pass review

Dispatch TWO agents in parallel:
1. `claude` (Opus, security focus): full audit of the 4 primitives against Lin/Li/Chen §12.1 contract — verdict RED/YELLOW/GREEN per §3.3
2. `code-quality-reviewer` (Opus): structural quality + mock-vs-prod divergence audit

Both must return YELLOW or GREEN before `brain-W1-RELEASE` opens.

Any RED → orchestrator dispatches fix-wave beads (`brain-W1-FIX-N`), wires them as blockers of W1-R0, re-runs review.

---

### Task brain-W1-RELEASE: tag brain-v0.2.0-neurosymbolic-alpha

```bash
cd /Users/erato949/Documents/optivai-claude-plugin
git tag -a brain-v0.2.0-neurosymbolic-alpha -m "MS_eps foundation: PV + RB + VF_eps + Hebbian"
git push origin brain-v0.2.0-neurosymbolic
git push origin brain-v0.2.0-neurosymbolic-alpha
```

Capture release thought to brain:
```bash
python3 scripts/open_brain.py --capture "Released brain-v0.2.0-neurosymbolic-alpha — Wave-1 MS_eps foundation operational (PV/RB/VF_eps/Hebbian). 4 primitives + 4 new MCP-equivalent CLI commands + ~80 new tests. Procurement claim: 99.9999793% confidence on verified forgetting at n=300/k=0/eps=0.05 (exact binomial)."
```

Close `<W1REL>`.

---

## 5. Wave-2 — Magic Moments

Wave-2 follows the exact same architect→TDD→review→close pattern as Wave-1 per task. Specs are shorter (~400-600 LOC) because patterns are established. Beads still atomic per task.

### Task brain-W2-S1: Citation walker (`--trace`)

**Reference:** `optivai-builder/src/agents/citation-walker.ts` (329 LOC). Walks `was_derived_from` recursively, returns a tree of `{thought_id, raw_text_preview, prov_agent, prov_activity, depth, children: [...]}`.

**Files:**
- Create: `scripts/citation_walker.py` (~200 LOC)
- Modify: `scripts/open_brain.py` — add `--trace <id> [--max-depth N]` subcommand
- Test: `tests/test_citation_walker.py`

**Acceptance gate:** `python3 scripts/open_brain.py --trace <id>` returns the full provenance tree of a thought, walking the `was_derived_from` chain to the original (depth-0) ancestor, with cycle detection (max depth 50 by default).

### Task brain-W2-S2: Citation walker test corpus

8+ tests: linear chain, branching tree, cycle detection, max-depth enforcement, cross-user isolation (citation must NOT leak across users), orphan derivation (`was_derived_from` set but parent was forgotten — should yield a "[orphaned]" sentinel node).

### Task brain-W2-S3: Inspect-memory (`--inspect <id> --at <ts>`)

**Reference:** `optivai-builder/src/agents/time-travel.ts:1-400` (InspectMemory class).

**Files:**
- Create: `scripts/time_travel.py`
- Modify: `scripts/open_brain.py` — add `--inspect <id> [--at <ISO-timestamp>] [--at-revision N]`
- Test: `tests/test_inspect_memory.py`

**Acceptance gate:** Given a thought with revisions at t1, t2, t3, `--inspect <id> --at <t2>` returns the state of the thought at t2. With no `--at`, returns latest. With `--at-revision 1`, returns revision 1's content.

### Task brain-W2-S4: Inspect-memory test corpus

8+ tests covering temporal queries, revision queries, queries before first revision (must return null + clear error), cross-user isolation.

### Task brain-W2-S5: brain.replay_log schema

**Reference:** `optivai-builder/src/security/audit-log.ts` + the v0.21 W3-REPLAY-LOG implementation (commit `687ca66`).

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS brain.replay_log (
    event_id        BIGSERIAL         NOT NULL PRIMARY KEY,
    user_id         VARCHAR(100)      NOT NULL,
    session_id      VARCHAR(200),                          -- nullable for CLI-direct calls
    event_type      VARCHAR(50)       NOT NULL,            -- 'capture'|'search'|'forget'|'promote'|'demote'|'rollback'|'inspect'|'trace'
    thought_id      VARCHAR(64),                           -- subject of the event (nullable)
    query_redacted  TEXT,                                  -- search query with PII redacted
    result_summary  TEXT,                                  -- 100-char preview, PII-redacted
    pii_distinct    BOOLEAN           NOT NULL DEFAULT TRUE,  -- PII redaction discipline marker
    trace_id        VARCHAR(64),                           -- OTel trace correlation (optional)
    span_id         VARCHAR(64),                           -- OTel span correlation (optional)
    prov_agent      VARCHAR(100)      NOT NULL,
    metadata        JSONB,                                 -- type-specific extra fields
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_replay_log_session ON brain.replay_log (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_replay_log_user_time ON brain.replay_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_replay_log_thought ON brain.replay_log (thought_id) WHERE thought_id IS NOT NULL;
```

**PII redaction rule:** the architect spec MUST adopt the same `PII_PATTERNS` regex set already in `open_brain.py` (find via `grep -n "PII\|redact" scripts/open_brain.py`). NEVER store `raw_text` in replay log — only `result_summary` (≤100 chars, redacted). NEVER store search queries verbatim — only `query_redacted`.

### Task brain-W2-S6: Replay log capture instrumentation

Modify every public function in `open_brain.py` (`capture_thought`, `search_thoughts`, `forget_with_verification`, `promote_thought`, `demote_thought`, `rollback_thought`, `inspect_thought`, `trace_citation`) to emit a `brain.replay_log` row before returning.

Use a decorator pattern to keep the audit emission DRY:

```python
def replay_logged(event_type: str):
    """Decorator that emits a replay_log row after the wrapped function returns."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            _emit_replay_log(event_type, fn, args, kwargs, result)
            return result
        return wrapper
    return decorator

@replay_logged("capture")
def capture_thought(...): ...

@replay_logged("search")
def search_thoughts(...): ...
```

### Task brain-W2-S7: `--replay` CLI

`python3 scripts/open_brain.py --replay --session-id <id> [--user <u>] [--from <ts>] [--to <ts>]` returns chronological log of all brain operations in that session. Useful for "what did the agent retrieve before making decision X" investigations.

### Task brain-W2-S8: Replay log test corpus

10+ tests: PII redaction completeness, session_id correlation, user isolation, OTel trace_id propagation (if `OTEL_TRACE_ID` env var is set, it's captured), audit gap detection (every public op MUST emit one row — test by counting before/after).

### Task brain-W2-D1: Pi plugin skill-doc sync

**Files:**
- Modify: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-search/SKILL.md`
- Modify: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-capture/SKILL.md`
- Create: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-forget/SKILL.md`
- Create: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-trace/SKILL.md`
- Create: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-inspect/SKILL.md`
- Create: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-promote/SKILL.md`
- Create: `/Users/erato949/Documents/optivai-pi-plugin/skills/brain-replay/SKILL.md`
- Modify: claude-plugin equivalents under `.claude/commands/` (e.g., `/brain-forget.md`)

Single bead, dispatched to `technical-writer` (Sonnet). One commit per repo. Each new skill ≤ 80 lines (description, when-to-use, command, examples). Read `optivai-builder/docs/wave2-neurosymbolic-handoff.md` for the OptivAI tool naming conventions.

### Task brain-W2-R0: Wave-2 dual-pass review + brain-W2-RELEASE

Same pattern as W1-R0. Tag `brain-v0.2.0-neurosymbolic-beta`. Capture release thought.

---

## 6. The Dependency Graph (Visual)

```
                  brain-W1-S0 (branch + DAG + scaffolding)
                       |
            +----------+------------+
            v                       v
       brain-W1-S1 (PV schema)   brain-W1-S10 (Hebbian schema)
            |                       |
   +--------+--------+               v
   v                 v          brain-W1-S11 (--promote/--demote)
brain-W1-S2 (capture) brain-W1-S4 (RB schema)    |
   |                 |          brain-W1-S12 (Hebbian tests)
   v                 v
brain-W1-S3 (PV    brain-W1-S5 (RB CLI)
  tests)             |
   |                 v
   |          brain-W1-S6 (RB tests)
   |                 |
   v                 |
brain-W1-S7 (VF probe)
   |
   v
brain-W1-S8 (--forget CLI)
   |
   v
brain-W1-S9 (VF tests)
   |
   +-----+-----+-----+
                     v
              brain-W1-R0 (dual-pass review)
                     |
                     v
              brain-W1-RELEASE (alpha tag)
                     |
       +-------------+-------------+
       v             v             v
  brain-W2-S1   brain-W2-S3   brain-W2-S5
  (citation)    (inspect)     (replay schema)
       |             |             |
       v             v             v
  brain-W2-S2   brain-W2-S4   brain-W2-S6 (replay instrumentation)
                                    |
                                    v
                              brain-W2-S7 (replay CLI)
                                    |
                                    v
                              brain-W2-S8 (replay tests)
                                    |
       +-----+-----------+----------+
                         v
                   brain-W2-R0 (dual-pass review)
                         |
                         v
                   brain-W2-D1 (Pi skill sync)
                         |
                         v
                   brain-W2-RELEASE (beta tag)
```

Total Wave-1+2: **26 beads**, ~4-5 weeks at 2-3 beads/week with proper review gates.

---

## 7. Wave-3 — Explicitly Deferred

These are documented but NOT scoped in this plan. They live as `brain-W3-DEFERRED-*` beads created post-W2-RELEASE:

- **Atomspace facade** (`brain.atoms` table with typed `kind` column beside flat thoughts) — bigger lift; defer until customer demand surfaces
- **NAL truth values + `--nal-infer`** — needs atomspace first
- **CONSOLIDATE worker** — needs NAL Truth_Revision
- **TRUST propagation** — N/A for plugin substrate (no tool-output linkage)
- **A/B clone-and-validate self-modification** — Level-C, lab-only per [[project_neurosymbolic_rd]]
- **MeTTa sidecar** — out-of-scope per strict Level-B-only stance

---

## 8. Risks + Mitigations

| Risk | Mitigation |
|---|---|
| Mock-vs-prod divergence (UUID/TEXT, CJS/ESM class) — see [[pattern_mock_vs_prod_divergence]] | Every implementer prompt requires running against a REAL Neon instance (use a `brain-test` Neon project; `DATABASE_URL_TEST` env var) — not just SQLite/in-memory |
| Implementer ships uncommitted orphans — see [[session-2026-05-20-21-wave3-wave4-marathon]] §execution-discipline | The 7-step git workflow in §3.1 is verbatim in every prompt; orchestrator runs `git status` independently before closing any bead |
| `implementer` agent type runs sandboxed | Use `implementation-developer` UNIVERSALLY for code; the plan never calls `implementer` |
| Pre-commit hook (P16-S4 sync-conflict guard) blocks commits | Run `npm run install:hooks` once at branch cut; verify with `ls .git/hooks/pre-commit` |
| Schema migration on a partner's Neon breaks existing data | Migrations are additive only (no DROP); backfill pattern in S1 ensures non-destructive upgrade; document in `docs/MIGRATION_GUIDE.md` |
| Forget exposes residue via embedding-NN that probes miss | Wave-3 backlog item: implement embedding-NN probe via real vector search (current first-cut uses search-by-text only). File as `brain-W3-VF-EMBEDDING-NN-PROBE` post-release |
| Test corpus too thin → bug ships | Min 10 tests per primitive; R0 review explicitly checks coverage |

---

## 9. Out-of-band assumptions

- The Neon free-tier DB has 0.5GB; PROV-DM adds ~200 bytes/thought + RB versioning grows linearly with edits. Project: ~5000 thoughts × 200 bytes ≈ 1MB additional PROV; versioning at 3 revs avg × 1KB ≈ 15MB. Well under quota.
- `jsonpatch` PyPI library is permissively licensed (MIT). Add to `requirements.txt` in S5.
- All work uses the `brain-v0.2.0-neurosymbolic` branch. No PR to main until W2-RELEASE; then squash-and-merge OR keep branch alive as the partner-distribution branch (Chris decides at release).

---

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-05-21-brain-neurosymbolic-port.md`. Two execution options:**

1. **Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration in this conversation. Best for staying close to the work; bead-by-bead orchestration with you in the loop.

2. **Parallel Session (separate)** — Open new session with `superpowers:executing-plans` in a fresh worktree, batch execution with checkpoints, rejoin to verify wave boundaries. Best if you want this to run while you do other things and check progress at the wave boundaries.

**Which approach?**
