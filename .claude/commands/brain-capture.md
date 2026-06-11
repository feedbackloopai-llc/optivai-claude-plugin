Commit something to memory. I want to remember this.

If the user provided text: $ARGUMENTS

If $ARGUMENTS is empty, ask what they want me to remember. Help them structure it:
- **Decision:** "Decided to [X] because [Y]. Alternatives considered: [A, B]."
- **Person note:** "[Name] — [role/context]. Talked about [topic]. Key takeaway: [insight]."
- **Meeting:** "Met with [people] re: [topic]. Decided: [list]. Actions: [list]."
- **Preference:** "Always [do X] / Never [do Y]. Reason: [why]."
- **Pattern:** "[Approach] works well for [situation]. Learned this when [context]."
- **Impression:** "[Person/system] is [observation]. Evidence: [what I noticed]."

Capture by running open_brain.py with the --capture flag. Use the installed path (`~/.claude/hooks/open_brain.py`) or the repo path (`scripts/open_brain.py`) — whichever exists.

```bash
python3 ~/.claude/hooks/open_brain.py --capture "<formatted thought>" --source "claude-code" --session-id "$CLAUDE_CODE_SESSION_ID" --project "<current_project_name>"
```

Replace `<current_project_name>` with the actual current project/directory name.

After capturing, briefly confirm what I remembered and what metadata was extracted.

## Seeding NAL truth values (T2.6)

By default, the truth-value `{frequency, confidence}` (stv) is derived from the confidence label Cortex extracts from the capture text (high→`c=0.9`, medium→`c=0.7`, low/absent→`c=0.5`; frequency defaults to `1.0`). You can override this explicitly:

- `--stv-f FREQ` — set stv frequency (0.0–1.0). Use `0.0` for a fully-refuted belief, `1.0` for strong positive evidence, values between for partial support.
- `--stv-c CONF` — set stv confidence (0.0–1.0). Represents weight of evidence; higher = more observations backing this belief. Confidence of `0.35` or below causes search results to display a `[LOW-CONFIDENCE]` marker.

```bash
python3 ~/.claude/hooks/open_brain.py \
  --capture "Postgres HNSW index recall ≥ 0.95 at 1M vectors (measured 2026-05-01)" \
  --stv-f 1.0 --stv-c 0.85 \
  --source "claude-code" --session-id "$CLAUDE_CODE_SESSION_ID" --project "optivai-builder"
```

These flags pair with `/brain-revise`: if you later find a contradicting atom, `--revise` will fuse the two stv values via NAL evidential-horizon revision. Seeding accurate `stv-c` values at capture time makes future revisions more meaningful — the weighted average is only as good as the input confidence values.

## Optional PROV-DM fields (v0.2.0+)

For explicit W3C PROV-DM provenance, the following flags are supported. The default capture path stamps these automatically from session context, but you can override when the thought derives from a specific upstream source (e.g. summarising a meeting note, transforming a prior decision):

- `--prov-agent <name>` — who/what produced this thought (e.g. `claude-code`, `ralph-loop`, `chris-manual`).
- `--prov-activity <verb>` — the producing activity (`capture`, `summary`, `synthesize`, `transform`).
- `--derived-from <thought_id>` — the parent thought this one derives from; populates `was_derived_from` and makes the new thought walkable by `/brain-trace`.

Use these when capturing a derived thought you expect to surface in a citation chain. `/brain-trace` will then walk the `--derived-from` pointer back to the source.

## Why this command exists in the neurosymbolic discipline

This is the agent's instrument for **Rule 3 — Capture-with-alternatives** and the registration half of **Rule 5 — Skill-lifecycle**. The MS_ε primitive enacted is `WA` (Write Authorization): every capture passes schema validation, RE2 PII redaction, and PROV-DM stamping before the atom commits. Anonymous writes are rejected at the type level — the resulting atom carries `{agent, activity, wasGeneratedBy, wasDerivedFrom?, sourceUri?}` so `/brain-trace` can walk its lineage later. Capture is the move that turns reasoning into institutional memory; skipping it discards the audit trail every prior agent has been building.

## When to invoke

- After making a decision of consequence — architectural choice, dependency selection, an approach that closes off other paths. The capture body MUST include `alternatives_rejected:` with the choices not taken (Rule 3). Plain capture without alternatives reads as "I picked X" and surfaces no learning to future agents.
- When you discover a reusable pattern — a sequence of moves that worked for a class of problems and is likely to apply again. Capture as `type=pattern`, then call `/brain-promote <id>` so the next similar task recalls it before re-deriving (Rule 5).
- When the user states a preference, correction, or "always do X / never do Y" rule — capture as `type=preference` immediately so the next session inherits it.
- When you learn something about a person — capture as `type=person_note` with the context, role, and the specific signal you observed.
- When two recalled atoms conflict and you resolve them via NAL revision, capture the fused belief with `--derived-from` pointing at both premises so the resolution is auditable (Rule 2).

## How to use the result

- Confirm the returned `thought_id` and the metadata Cortex extracted (`type`, `topics`, `people`, `summary`, `confidence`). If extraction got the type wrong, immediately re-capture with the corrected framing rather than letting the wrong kind drift downstream.
- For derived captures, verify the `was_derived_from` field is populated by running `/brain-trace <new_id>` and confirming the chain walks back to the intended parent.
- If the captured atom matters more than its raw similarity score will likely surface, call `/brain-promote <id>` once after capture to seed Hebbian weight (Rule 4).
- If the user immediately corrects the capture ("no, the alternative I rejected was different"), do NOT call `/brain-forget`; capture a corrective atom with `--derived-from` pointing at the wrong one and `/brain-demote` the wrong one. Forgetting is reserved for the user.

## Pearl atom-kind hint

Capture kind selection should map to the closed enum at `src/contract/pearl/index.ts:12` (`ATOM_KINDS`, 27 total kinds). Defaults for common agent intents:

| Capture intent | Pearl kind |
|---|---|
| Decision with rationale + alternatives | `fact` (the `decision` type captures the framing) |
| Learned reusable pattern | `skill_ref` (the `pattern` type captures the framing) |
| Architectural choice that commits future work | `Artifact` + `Policy` pair |
| Observed event with no derivation | `atom` |
| Subagent dispatch trace | `SubagentDispatch` / `SubagentTrace` |
| User preference, "always/never" rule | `Policy` |
| Proposed work item not yet started | `Intent` with status `proposed` |
| Active or completed work item | `Intent` with status `active` / `done` |
| Reflection or self-critique | `AssemblerDecisionTrace` |
| KPI definition or observation | `KPIDefinition` / `KPIObservation` |
| AHA / OSSA verdict | `AHAVerdict` / `OSSAVerdict` |

When in doubt, `atom` is the safe default; the classifier never invents kinds outside the closed enum. The Claude Code `type=` value (`decision`, `pattern`, `preference`, etc.) is the user-facing handle; Pearl kind is the substrate-facing taxonomy. Both are stored.

## Example

You commit to using `pgvector` for the embedding store after evaluating Pinecone and Qdrant. The capture is:

```bash
python3 ~/.claude/hooks/open_brain.py --capture "Decision: use pgvector for the embedding store. Alternatives rejected: Pinecone (vendor lock-in, no on-prem option), Qdrant (operational overhead at 1-user scale not justified). Reasoning: shared infra with existing Postgres; HNSW recall ≥ 0.95 measured on prior benchmark; reversible if scale exceeds 10M vectors." --source "claude-code" --session-id "$CLAUDE_CODE_SESSION_ID" --project "optivai-builder"
```

Cortex extracts `type=decision`, `topics=[pgvector, embedding-store, vector-db]`, `confidence=0.92`. Six weeks later another agent runs `/brain-search "vector database choice"` and surfaces this atom with its alternatives and reasoning intact — no re-derivation, and the rejected alternatives are themselves searchable when the question becomes "why didn't we use Pinecone?"
