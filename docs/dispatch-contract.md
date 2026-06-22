# Subagent Dispatch Contract

**Status:** active · **Enforced by:** `dispatch_gate.py` (Claude Code PreToolUse) + `dispatch-gate.ts` (Pi `tool_call`) · **Bead epic:** `epic:dispatch-gate`

## Why

Analysis of 1,479 real subagent dispatches (2026-06-18) showed the prompts are mostly well-formed — **97% reference file paths** (not pasted content) and median ~936 tokens. The gap: **only 38% state an explicit termination/acceptance criterion.** A subagent without a crisp "done condition" runs longer, generates more output (the expensive part — its own context burn), and is more prone to returning an unverifiable or hallucinated success report. This contract makes the done-condition a first-class, gate-enforced element of every dispatch.

The gate is **advisory by default** (warn, never block). It nudges; it does not police. Fail-open always: any error in the gate allows the dispatch.

## A compliant dispatch prompt contains

In order:

1. **Objective / role** — who the subagent is and the single goal.
2. **Bead id + repo + branch** — the unit of work and where (when applicable).
3. **Paths to read — never paste the file** — reference files by absolute/repo-relative path; the subagent reads them itself.
4. **Termination / acceptance criterion (the goal point)** — an explicit statement of what "done" means or what must be returned for the task to be complete. **This is the primary gate check.**
5. **Output contract** — what to return (a structured summary, not a raw dump).

## Gate behavior

| aspect | rule |
|---|---|
| **Scope** | Fires ONLY on subagent-dispatch tools — Claude Code `Task`/`Agent`, Pi `subagent`. Every other tool passes through untouched. |
| **Modes** (`DISPATCH_GATE_MODE`) | `warn` (default) — emit advisory, allow. `strict` — block dispatches missing the termination criterion. `off` — passthrough. |
| **Fail-open** | Any exception, missing field, or unparseable input → **allow**. The gate never breaks a dispatch. |
| **Empty/trivial** | Empty prompt → allow. Prompts shorter than `DISPATCH_GATE_MIN_PROMPT_TOKENS` (default 150) skip the content-paste check (trivial tasks don't need the full contract). |

### Verdict shape (both implementations return this)

```
{
  "checked": bool,          # false when tool is out of scope or mode=off
  "compliant": bool,        # no missing[] and no warnings[]
  "missing": [str],         # high-severity: contract elements absent
  "warnings": [str],        # low-severity nudges
  "block": bool             # true only when mode=strict AND termination criterion missing
}
```

## Validation rules (identical semantics in Python and TS)

Apply to the dispatch **prompt** string. All regexes are **case-insensitive**.

**Rule 1 — Termination criterion (→ `missing` if absent; drives `block` in strict mode).**
Present if the prompt matches:
```
acceptance|done when|success criteri|deliverable|definition of done|stop when|when (you('re| are))? ?(complete|finished|done)|complete when|expected (output|result|behavior)|must (return|produce|deliver|output)|return (only|a |the |json|the following)|criteria:|verify that
```

**Rule 2 — Redundant content paste (→ `warnings`; skipped when prompt < `MIN_PROMPT_TOKENS`).**
Warn if the prompt contains a fenced block (```` ``` ````) whose inner length ≥ `DISPATCH_GATE_MAX_EMBED_CHARS` (default 1500) **AND** also references a file path (Rule-path regex below). Message names the embedded size and suggests passing the path. (Embedding a *target spec/test* is legitimate; this only fires when a large paste co-exists with a path reference — the redundant case.)

**Rule 3 — Output contract (→ `warnings` if absent).**
Present if the prompt matches:
```
return |report|output|summary|respond with|provide (a|the)|hand back|deliver(able)?|produce (a|the)
```

**Path regex (used by Rule 2):**
```
/[\w.-]+/[\w.-]+\.(py|ts|tsx|js|mjs|md|sql|sh|ps1|json|ya?ml)|\b(scripts|src|docs|tests?|hooks)/
```

### Env knobs

| var | default | meaning |
|---|---|---|
| `DISPATCH_GATE_MODE` | `warn` | `warn` / `strict` / `off` |
| `DISPATCH_GATE_MIN_PROMPT_TOKENS` | `150` | below this (chars÷4), skip Rule 2 |
| `DISPATCH_GATE_MAX_EMBED_CHARS` | `1500` | Rule 2 fenced-block threshold |

## Shared test corpus

Both test suites load `dispatch_corpus.json` (canonical at `scripts/hooks/tests/dispatch_corpus.json`, byte-copied to the Pi test dir). Each entry is `{name, prompt, mode, expect: {compliant, missing_has?, warnings_has?, block}}`. Parity test T3 asserts the Python and TS validators produce the **same** verdict for every corpus entry. Adding a case to the corpus automatically covers both languages.

## Warn-mode output

- **Claude Code:** the PreToolUse hook emits `hookSpecificOutput.additionalContext` with a one-block advisory (visible to the orchestrator), exit 0. In `strict` it returns `permissionDecision: "deny"` with the reason.
- **Pi:** the `tool_call` handler calls `ctx.ui.notify(...)` / logs the advisory and returns `undefined` (allow). In `strict` it returns `{ block: true, reason }`.
