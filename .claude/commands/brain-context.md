Recall my recent context — what do I remember and what's pending?

Run ALL three (use `~/.claude/hooks/open_brain.py` or `scripts/open_brain.py` — whichever exists). Suppress errors gracefully if commands fail.

1. Recent memories (last 3 days):
```bash
python3 ~/.claude/hooks/open_brain.py --recent --days 3 --json
```

2. Memory patterns:
```bash
python3 ~/.claude/hooks/open_brain.py --stats --json
```

3. Pending work:
```bash
beads ready 2>/dev/null || true
beads list -g --label handoff 2>/dev/null || true
```

Synthesize naturally:
- "I remember working on [X] recently. Key decisions: [Y]. People involved: [Z]."
- "I have [N] pending tasks. The most urgent: [list top 3]."
- "My strongest knowledge areas: [top topics]. People I know well: [top people]."

This is my session primer — I use this to not start from zero.

## Why this command exists in the neurosymbolic discipline

This is the **full-reload flavor of Rule 1 — Recall-before-reason**. Where `/brain-search` is topical and `/brain-recent` is a time window, `/brain-context` is the propulsive opener that hydrates working memory in one move: recent atoms (PS-scoped), atom-store distribution (substrate observability), and beads-ready (open-work queue). The MS_ε primitive enacted is `PS` (Principal Scoping) plus a peek at the substrate aggregate. Without this, a fresh session starts from zero and the agent re-asks questions whose answers were captured yesterday.

## When to invoke

- At session start, before the user prompts. Pairs with `beads ready` to surface queued work; the Propulsion Principle is: "when an agent finds work on its hook, it executes" (Rule 1, propulsive opener).
- After a context compaction or model reset — re-hydrate everything in one move instead of asking the user to repeat state.
- When returning from a multi-day break — the recent window + open beads + topic distribution together reconstruct enough to resume confidently.
- Before estimating effort on a request — knowing what is already known shapes the size of the new work.

## How to use the result

- Lead with pending work, then recent decisions, then knowledge areas. This is the order that surfaces "do you want to continue X?" before asking "what's next?"
- If recent atoms point at an in-flight decision and beads-ready shows a related task, propose resuming that task rather than asking the user to choose.
- If the stats summary surfaces a topic you don't have direct context on and the user mentions it, follow up with `/brain-search "<topic>"` to drill in (Rule 1, topical refinement).
- If a recent atom unexpectedly mattered for the current session — e.g., re-validated by the user's first prompt — call `/brain-promote <id>` so the Hebbian boost protects it from time decay (Rule 4).

## Example

Session opens, you run `/brain-context`. Output: recent atoms include yesterday's decision to defer Wave-5 chat-pane work and a reflection on the parallel-implementer race pattern. Beads-ready shows three unblocked tasks, the top one labeled `handoff` from yesterday's session. Stats show your strongest topic clusters are OptivAI v0.19+ neurosymbolic substrate and Wave-C universal artifact surface. You open with: "Yesterday's session left a handoff bead for the artifact-surface Sprint-8 collector wiring, and Wave-5 chat-pane is still deferred. Picking up Sprint-8?" The user confirms, you proceed without a "what would you like to do?" round-trip.
