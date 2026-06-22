---
name: gensi-prompt-audit
description: Audits prompt text (SKILL.md, agent definitions, slash commands, tool descriptions, system prompts) for Claude Opus 4.7 readiness defects across 8 lenses (SI/IC/LIR/AE/TS/OC/SCL/TDQ). Returns prioritized, remediation-ready findings. Use when reviewing prompt-bearing files after a model upgrade, during the biweekly retool cadence, or before shipping a new agent or skill.
allowed-tools: Read, Bash, Grep, Glob, WebFetch
---

# gensi-prompt-audit — 8-Lens Prompt Audit for Claude Opus 4.7

Audits one file or a directory of prompt-bearing files (SKILL.md, agent .md, slash commands, tool descriptions, system prompts, MCP server descriptions) against the 8-lens framework for Claude Opus 4.7 readiness. Returns a prioritized findings list using the format prescribed by the source gist.

**Source authority:** https://gist.github.com/subourbonite/22113b538602832a68a41a623fdeea76
**Mechanical prefilter scripts (Pi-Mono):** `~/Documents/Pi-Mono/docs/prp/audit/_build-inventory.py`, `_build-findings.py`
**PRP record:** `~/Documents/Pi-Mono/docs/prp/PRP-2026-05-11-tooling-retool-claude-opus-4-7.md`

## When to invoke

Invoke this skill when:
- A new prompt-bearing file (agent, SKILL.md, slash command, tool description) is about to ship.
- Anthropic releases a new Opus tier and the biweekly retool review is due.
- A downstream agent fails in ways that smell like prompt drift (over-firing, under-firing, refusing valid tasks, ignoring constraints).
- Chris asks for "audit this prompt" or "8-lens check" or "is this 4.7-ready".

Skip this skill when:
- The target is non-prompt code (Python, TS, SQL) without embedded prompt text.
- The target is documentation never read by a model at runtime (`CHANGELOG.md`, `README.md` outside of prompt-loading paths).
- The change is a one-character typo fix where running the audit costs more than the bug.

## The 8 Lenses

| Code | Lens | What it checks |
|---|---|---|
| **SI** | Structural Integrity | Every conditional has all branches. Every referenced path, agent, or tool resolves. Frontmatter present and complete on loaded agents. |
| **IC** | Instruction Clarity | Imperative voice for actions, declarative for rules. One term per concept. Pronouns have a single antecedent. No double negatives. |
| **LIR** | Literal Interpretation Risk | Universal quantifiers (`every`, `each`, `all`) on set operations. Specific verbs near fragile ops (write, emit, delete). Conditions literally evaluable. |
| **AE** | Attention Economy | Emphasis markers (CAPS, **bold**, MUST, NEVER, CRITICAL) used sparingly — each carries signal. ≤3 emphasis tokens per file is the working threshold on 4.7. Motivate restrictions; lead with positive framing. |
| **TS** | Trigger Specificity | Every tool or subagent mention has a when-to-use clause. Parallel dispatch uses the canonical snippet. Capability-framed references ("you can use X") replaced with imperatives ("Use X when [condition]"). |
| **OC** | Output Contract | Length stated positively ("respond in ≤200 words"). Format defined via schema, template, or example. No dependence on assistant prefill (behavior changed on 4.7). |
| **SCL** | Schema Constraint Legality | JSON Schema uses only supported keywords. `additionalProperties: false` for strict mode. Numeric constraints (min/max/multipleOf) handled correctly. |
| **TDQ** | Tool Description Quality | Calm imperative voice — no aggressive mandate. When-to-invoke guidance on every tool. Parameter calibration matches Claude's tool-use conventions. |

**Severity levels:**
- **CRITICAL** — Wrong behavior on 4.7; fix before production.
- **HIGH** — Wrong behavior sometimes (effort- or input-dependent); fix before handoff.
- **MEDIUM** — Recoverable defects; fix when touching adjacent code.
- **LOW** — Excluded from quick mode.

## Usage

Invoke with a target path (file or directory):

```
/gensi-prompt-audit ~/.claude/agents/code-quality-reviewer.md
/gensi-prompt-audit ~/.pi/agent/agents/
/gensi-prompt-audit ~/dev/optivai-claude-plugin/skills/
```

Or in conversational form:
> "Run the 8-lens audit on `~/.claude/agents/code-quality-reviewer.md`."
> "Quick audit this skill before I ship it."

## Mode selection

**Quick mode** — Default for a single file or a short inline prompt (<400 lines, single surface).
- One Claude pass.
- Evaluates SI, LIR, AE, TS, OC (the five fastest-paying lenses).
- Emits up to 12 findings, ordered CRITICAL → HIGH → MEDIUM.
- IC, SCL, TDQ noted as "scope gaps" if not deeply checked.

**Deep mode** — Use when the target is a directory or pipeline (≥5 files, cross-file dependencies).
- Step 1: Run the Python prefilter (`_build-inventory.py` then `_build-findings.py`) to catch mechanical defects (missing frontmatter, retired sampling params, emphasis density, capability-framed references).
- Step 2: For every file with prefilter findings or >300 lines, run a Claude pass with all 8 lenses.
- Step 3: Cross-file consistency pass — same concept named identically across files; no duplicate agents with divergent instructions; one antecedent per shared term.
- Step 4: Synthesize findings into a single ordered list with per-file groupings.

## Execution steps

1. **Resolve target.** If the argument is a directory, list all `*.md`, `*.ts`, `*.py` files containing prompt text (system prompts, tool descriptions, MCP server descriptions). If it is a file, read it directly.

2. **Read one-hop references.** When the target references other files (loaded agents, skills, hooks, included system prompts), read those as well. Do not recurse beyond one hop.

3. **Run the prefilter (Deep mode only).** Execute:
   ```
   python3 ~/Documents/Pi-Mono/docs/prp/audit/_build-inventory.py
   python3 ~/Documents/Pi-Mono/docs/prp/audit/_build-findings.py
   ```
   The output `findings.json` contains mechanical findings indexed by file. Use it to seed the Claude pass.

4. **Evaluate against the 8 lenses.** Use the Claude quick-review template below, parameterized for the target.

5. **Emit findings.** Use the prescribed format. Order by severity, then by lens. Group by file when there is more than one.

6. **Recommend next steps.** End with one of: "ready", "minor fixes", "substantial revision", or "rework needed".

## Quick-review template (parameterize and run)

```
Review the prompt at {{TARGET_PATH}} for Claude Opus 4.7 readiness defects.

Target type: {{TARGET_TYPE}}        # skill-md | standalone-prompt | embedded-template | tool-description | agent-md
Execution context: {{CONTEXT_NOTES}}  # caller, effort level, output consumers; "none" if standalone

Steps:
1. Read {{TARGET_PATH}} and one-hop referenced files.
2. Evaluate against SI, LIR, AE, TS, OC dimensions (Quick mode) or all 8 (Deep mode).
3. Produce findings ordered CRITICAL → HIGH → MEDIUM.

Output:
## Audit: {{TARGET_PATH}}
**Mode:** quick | deep
**Overall:** ready | minor fixes | substantial revision | rework needed
**Findings:** (up to 12 in Quick mode; unbounded in Deep mode; use the format below)

### [SEVERITY] · [DIMENSION] · [LOCATION]
**Offending text:** "[verbatim quote, ≤25 words]"
**Why on 4.7:** [one sentence on model behavior]
**Remediation:** [exact replacement; fenced block if >10 words]

**Scope gaps:** [lenses not deeply checked; cross-file caveats; references unread]
```

Location format: `filename:line-range` (e.g., `code-quality-reviewer.md:42-47`) or `## Section > ### Subsection`.

## Output format spec (verbatim from gist)

Each finding follows this exact shape:

```
### [SEVERITY] · [DIMENSION] · [LOCATION]
**Offending text:** "[verbatim quote, ≤25 words]"
**Why on 4.7:** [one sentence]
**Remediation:** [exact replacement, fenced if >10 words]
```

Worked example:

```
### HIGH · AE · code-quality-reviewer.md:12-18
**Offending text:** "CRITICAL: You MUST ALWAYS verify before claiming done. NEVER skip this step. This is REQUIRED."
**Why on 4.7:** Five emphasis tokens in three sentences dilute each other; 4.7 reads stacked CAPS as decorative and discounts the instruction.
**Remediation:**
```
Verify all tests pass before reporting completion. If verification fails, fix the underlying issue and re-run.
```
```

## What this skill misses

The skill is a prompt-text audit. It does not catch:
- Runtime dispatch bugs (model-selector misrouting, install-script regressions, symlink breakage).
- Tool-use schema bugs in the executing code (TS/Python).
- Hook-script logic errors (`brain_hook.py`, `beads_writer.py`).
- Performance regressions, token-cost regressions, or rate-limit issues.

When the prompt audit looks clean but the agent still misbehaves, escalate to a runtime trace (logs in `.claude/logs/`) and check the dispatch path before re-auditing the prompt.

## Reference scripts (mechanical prefilter)

Two Python scripts at `~/Documents/Pi-Mono/docs/prp/audit/` perform mechanical checks before any Claude pass. They are fast and free; use them in Deep mode.

- **`_build-inventory.py`** — Walks every prompt-bearing surface (Pi agents, Pi skills, Claude agents, Claude commands, Claude hooks, OptivAI Claude plugin agents/commands, OptivAI Pi plugin agents/skills) and produces `inventory.json` plus `metrics-baseline.md` (per-file frontmatter coverage, emphasis-marker density, char/line counts).
- **`_build-findings.py`** — Reads `inventory.json`, applies regex heuristics for the eight lenses where they are mechanical (missing frontmatter → SI; emphasis density >3 → AE; capability-framed references → TS; vague verbs near write/output → LIR; retired sampling params → OC; double negatives → IC; tiny stub files → SI cull candidate). Produces `findings.json` and `findings-summary.md`.

These two scripts catch ~60-70% of audit findings in a single mechanical pass. Use them as Step 1 in Deep mode; reserve the Claude pass for semantic judgment (TDQ, complex TS, SCL).

## Biweekly retool cadence

Chris reviews prompt-bearing AI tooling on a biweekly cadence. Standard flow:

1. `cd ~/Documents/Pi-Mono/docs/prp/audit/`
2. `python3 _build-inventory.py && python3 _build-findings.py`
3. Read `findings-summary.md` for the top-20 files by finding count.
4. For each top-finding file: `/gensi-prompt-audit <path>` in Quick mode, remediate inline, commit with the bead ID.
5. For cross-file or systemic findings: open a new PRP, wire beads, treat as a deep retool.
