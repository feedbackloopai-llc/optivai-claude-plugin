What have I been thinking about recently?

```bash
python3 ~/.claude/hooks/open_brain.py --recent --days 7
```

Present grouped by date. If user specified a type or timeframe in $ARGUMENTS, adjust the --days and --type flags.

## Why this command exists in the neurosymbolic discipline

This is the lightweight, time-windowed flavor of **Rule 1 — Recall-before-reason**. The MS_ε primitive enacted is `PS` (Principal Scoping): results are tenant- and principal-scoped just like `/brain-search`, but the retrieval axis is recency rather than semantic similarity. Useful at session start and for "where did we leave off" reconstruction without needing a topical query. It is the cheapest recall the agent has — no embedding lookup, just a windowed read against the atom store.

## When to invoke

- At session start, alongside `beads ready`, to hydrate "what was this principal thinking about lately" before the user prompts (Rule 1 — Recall-before-reason, the propulsive opener).
- When the user opens with "where were we?", "what's happening?", or "catch me up" — answer from the recent window, not from your generic memory.
- Before resuming work after a break or context compaction — re-hydrate before re-deriving.
- When you need a low-cost orientation pass and don't yet have a specific topic to search on. Pair with `/brain-search "<topic>"` once a topic emerges.

## How to use the result

- Results group naturally by date. Lead with the most recent day's captures and walk backwards.
- If a recent atom looks load-bearing for the current task, follow up with `/brain-search` on its topic to widen the net beyond the window.
- If a recent atom contradicts the user's current statement or another recalled atom, escalate to `/brain-trace <id>` on both (Rule 2 — Conflict-fusion via NAL) rather than picking arbitrarily.
- Recent decision atoms are prime candidates for `/brain-promote` if they re-prove load-bearing during the current session (Rule 4).

## Example

Session opens. Before the user types anything specific you run `/brain-recent`. The output shows: yesterday — a decision to defer the Wave-5 chat-pane work pending a token-budget review; two days ago — a pattern capture about parallel-implementer git index races and the `git commit --only <paths>` mitigation. You open with "I see Wave-5 is deferred pending the token-budget review and the parallel-implementer race pattern is fresh — picking up from either?" instead of "what would you like to work on?" The agent has surfaced state rather than asking the user to repeat it.
