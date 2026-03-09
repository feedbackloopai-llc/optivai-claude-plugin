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
