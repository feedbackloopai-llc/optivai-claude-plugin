How is my memory distributed? What do I know most about?

```bash
python3 ~/.claude/hooks/open_brain.py --stats
```

Present stats readably — total memories, weekly trends, top topics, people, type distribution.

## Why this command exists in the neurosymbolic discipline

This is the **meta primitive** that informs the other rules rather than enacting any single one. It exposes the substrate aggregate — total atoms, type distribution, weekly capture rate, top topics, top people, knowledge-density hotspots. Useful as a calibration step before deciding which rule applies: if the topic the user just mentioned shows high atom-density in stats, jump straight to `/brain-search` (Rule 1) before reasoning; if it shows near-zero density, you can proceed and the absence is itself a finding worth capturing later. Reads only metadata — no atom payloads, no PROV-DM walk, no Hebbian weights.

## When to invoke

- Before deciding which recall command to use — stats can tell you whether a topic has 50 atoms (use `/brain-timeline`) or 2 atoms (`/brain-search` is enough).
- At session start as part of `/brain-context` — surfaces "what do I know best right now" without needing a topical query.
- When the user asks "what do you know about?" or "what topics dominate your memory?" — this is the direct answer surface.
- For periodic substrate-health review — flat capture rate may indicate the Rule 3 capture-with-alternatives discipline has slipped; absent topic clusters may indicate Rule 5 skill-lifecycle never got off the ground in that domain.

## How to use the result

- Top topics + top people are the cheapest "is this a known area" signal — if a topic the user is asking about does not appear in the top distribution, do not assume your recall is comprehensive; trigger `/brain-search` widely.
- Weekly trend lines surface capture cadence. A sudden drop after a sprint is usually a discipline slip (Rule 3 not firing on decisions); a sudden spike usually means an active project.
- If stats show a kind imbalance (e.g., 100 `decision` atoms but zero `pattern` atoms), that's a Rule 5 prompt — patterns are being re-derived rather than registered.
- Stats are read-only; no atom is being modified. No follow-up promote/forget action is appropriate from stats alone.

## Example

Mid-session, the user asks you to design a deduplication strategy for a new event stream. You run `/brain-stats` and see "event deduplication" does not appear in the top 30 topics; "stream processing" appears with 4 atoms. You decide a single targeted `/brain-search "event stream deduplication"` will not be comprehensive, so you broaden to `/brain-search "idempotency stream"` and `/brain-search "deduplication pattern"` in parallel. The breadth comes from knowing the substrate is thin on the specific topic, not from blind multi-querying.
