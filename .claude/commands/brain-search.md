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
