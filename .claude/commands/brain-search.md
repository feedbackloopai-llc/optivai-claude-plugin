Search my memory: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --search "$ARGUMENTS" --limit 10 --json
```

Parse the JSON results and present them conversationally:
- Show similarity percentage, summary, type, date
- Highlight topics and people mentioned
- If action items exist, surface them prominently
- Suggest follow-up searches if results are partial

If the user asks for chronological/time-ordered results, add `--sort time`:
```bash
python3 ~/.claude/hooks/open_brain.py --search "$ARGUMENTS" --limit 10 --sort time --json
```

If no results, suggest broadening the query or trying related terms.

## Why this command exists in the neurosymbolic discipline

This is the agent's primary instrument for **Rule 1 — Recall-before-reason**. The MS_ε primitive enacted is `PS` (Principal Scoping): the atomspace is queried under the caller's principal, so results are filtered to atoms the agent is authorized to surface. Semantic-vector retrieval finds atoms by meaning rather than literal token overlap, which is what makes "recall the policy on retry jitter" return the right pattern atom even when the original capture used different vocabulary. Without this step, the agent re-derives what the harness already knows and discards the audit trail every prior agent has been building.

## When to invoke

- Before substantive work on a topic begins — planning, designing, coding, reviewing, deciding. Pulls relevant prior decisions, patterns, and skills so you don't re-derive (Rule 1).
- When a person, system, API, or project is mentioned — search for prior context and known gotchas before forming a position.
- When making a decision — search for prior decisions on the same topic; if one exists, treat it as your starting point and check whether the current context invalidates it (Rule 2 may then apply).
- When the user asks "what did we decide about X?" or "have we seen this before?" — answer from memory, not from your generic training.
- When context feels thin and you're tempted to ask the user — search first; the answer may already be captured.

## How to use the result

- Each result carries `id`, `type`, `summary`, `similarity`, `created_at`, and any extracted `topics`/`people`. Lead with the highest-similarity atom whose type matches your need (`decision` for prior choices, `pattern` for reusable approaches, `preference` for user-stated rules).
- Results also carry `stv: {f, c}` — the NAL truth value for each atom. Atoms with `c < 0.35` display a `[LOW-CONFIDENCE]` marker; treat these as weak evidence and seek corroboration or run `/brain-revise` if a stronger complementary atom exists.
- An atom whose producing turn scored high on the persuasion-bombing detector (`/persuasion-score`) carries a `[LOW-VERACITY: produced under pushback]` label. This means the claim was captured while the turn was showing persuasion-bombing tells (escalating flattery, doubling-down, missing derivation) rather than genuine re-examination - the content may still be correct, but corroborate before acting on it, and if you cite it, cite it WITH the caveat rather than laundering it into a clean-sounding claim. VL-6 marginally demotes these atoms in ranking, so a `[LOW-VERACITY]` atom that still surfaces high in your results is both highly-relevant AND flawed - the label is its defense against uncritical citation, not grounds to omit it.
- If two results contradict each other, invoke `/brain-trace` on both before deciding which to trust. This serves Rule 2 — Conflict-fusion via NAL: the trace surfaces premises and PROV-DM lineage so the conflict can be resolved by provenance comparison rather than arbitrary tiebreak.
- If a recalled atom is older than 90 days, single-sourced, or load-bearing for the decision you're about to make, run `/brain-trace <id>` before acting on it (Rule 6 — Provenance-traversal).
- If a recalled atom turned out to be load-bearing after the fact — it prevented a regression, surfaced a non-obvious constraint — call `/brain-promote <id>` so the Hebbian signal protects it from time decay (Rule 4).
- Empty results are a real signal: document the absence in the reasoning trace and proceed; if the work succeeds, `/brain-capture` it so the next agent's recall is not empty.

## Example

You are about to design an HTTP retry policy. Before writing any code you run `/brain-search "http retry policy"`. Three results return: a 2024 decision atom selecting exponential backoff (similarity 0.82), a 2025 pattern note about jitter preventing thundering herd (0.79), and an older insight about idempotency-key reuse during retries (0.71). You start the design from those three, cite them in the new decision atom, and discover the jitter pattern would have caught a bug the first design draft contained. After shipping you `/brain-promote` the jitter atom so the next agent retrieving "retry" sees it ranked higher.
