Show how my thinking on a topic evolved over time: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --timeline "$ARGUMENTS" --days 90 --limit 50 --json
```

Parse the JSON results and present chronologically (oldest first) showing clear time progression.
- Lead with the topic name and time span covered
- Show each thought with date, type, and summary
- Highlight decision points and shifts in thinking
- If the user specifies a time range, pass --days accordingly

## Why this command exists in the neurosymbolic discipline

This is the **time-axis flavor of Rule 6 — Provenance-traversal**. Where `/brain-trace` walks the `wasDerivedFrom` chain (lineage axis), `/brain-timeline` walks the chronological sequence of atoms about a topic regardless of explicit derivation links. The MS_ε primitive enacted is `PV` (Provenance Visibility): the atoms surface with their PROV-DM stamps so the reader can see who captured what when and how positions shifted. Useful when you suspect the current recall has drifted from earlier evidence and you want to inspect the longitudinal record.

## When to invoke

- Before acting on a recall whose topic has clearly evolved — "what did we decide about X" returns one atom, but the question is whether that decision still holds (Rule 6 along the time axis).
- When the user asks "how did our thinking on X change?" or "show me the history of decisions about Y" — this is the canonical answer surface.
- During post-mortem or retrospective work — surfaces the actual sequence of decisions rather than the current synthesized view.
- Before reversing a prior decision — see the chronological context that produced the previous position; the reasoning may still apply.

## How to use the result

- Present chronologically oldest-first so the reader follows the evolution forward in time. Highlight inflection points: where an `impression` became a `decision`, where a `decision` was superseded by a new `decision`, where a `pattern` was first captured then later promoted.
- If two atoms in the timeline contradict, escalate to `/brain-trace <id>` on the conflicting pair (Rule 2 — Conflict-fusion via NAL) to inspect their respective derivations before resolving.
- If a recent timeline entry supersedes an older one whose `/brain-trace` shows it was load-bearing for other decisions, surface that downstream impact rather than silently treating the recent atom as authoritative.
- For deep-derivation questions ("which atoms cite this one as a source?"), `/brain-trace` is the right tool — timeline answers "what came before/after?" not "what derives from this?"

## Example

The user asks "where did we land on the parallel-implementer git-index race issue?" You run `/brain-timeline "parallel implementer git race"`. The output shows: two months ago — an incident note (the initial collision), six weeks ago — a pattern atom capturing `git commit --only <paths>` as mitigation, four weeks ago — a decision atom adopting the mitigation as a carryforward template, last week — a reflection that the Agent tool's `isolation: "worktree"` is the structural fix superseding the carryforward. You answer with the full arc rather than only the most recent atom, and you flag that the carryforward template is now redundant on worktree-isolated work.
