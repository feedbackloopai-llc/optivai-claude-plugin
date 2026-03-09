# Search Context Bundle Logs

Search through auto-logged agent activities and context bundle data.

## Arguments

- `$ARGUMENTS` - Required search query, optional `--days N` for time window

## Instructions

1. **Parse Search Query**: Extract the search term and any time modifiers
   - Main search query from `$ARGUMENTS`
   - If `--days N` specified, search last N days (default: 7)

2. **Execute Search**: Run the log search command
   ```
   python .claude/commands/activity_commands.py search-logs --query "[QUERY]" --days [DAYS]
   ```

3. **Present Results**: Show matching activities with:
   - Relevant context from when the activity occurred
   - File operations related to the search term
   - User prompts that mentioned the search term
   - Agent operations and results

4. **Highlight Relevance**: Explain why each result matches and how it might be useful for current work

5. **Suggest Follow-up**: Recommend related searches or next actions based on findings

Use this to find previous work related to your current task from the auto-logged context bundles.
