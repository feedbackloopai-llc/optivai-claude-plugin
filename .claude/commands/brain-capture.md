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

## Optional PROV-DM fields (v0.2.0+)

For explicit W3C PROV-DM provenance, the following flags are supported. The default capture path stamps these automatically from session context, but you can override when the thought derives from a specific upstream source (e.g. summarising a meeting note, transforming a prior decision):

- `--prov-agent <name>` — who/what produced this thought (e.g. `claude-code`, `ralph-loop`, `chris-manual`).
- `--prov-activity <verb>` — the producing activity (`capture`, `summary`, `synthesize`, `transform`).
- `--derived-from <thought_id>` — the parent thought this one derives from; populates `was_derived_from` and makes the new thought walkable by `/brain-trace`.

Use these when capturing a derived thought you expect to surface in a citation chain. `/brain-trace` will then walk the `--derived-from` pointer back to the source.
