Show how my thinking on a topic evolved over time: $ARGUMENTS

```bash
python3 ~/.claude/hooks/open_brain.py --timeline "$ARGUMENTS" --days 90 --limit 50 --json
```

Parse the JSON results and present chronologically (oldest first) showing clear time progression.
- Lead with the topic name and time span covered
- Show each thought with date, type, and summary
- Highlight decision points and shifts in thinking
- If the user specifies a time range, pass --days accordingly
