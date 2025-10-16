# Show Agent Activity History

Display recent agent activities from auto-logged context bundle data with filtering options.

## Arguments

- `$ARGUMENTS` - Optional time filter: `--hours 8`, `--max 20`, `--session current|all`

## Instructions

1. **Parse Arguments**: Check if user provided time filters
   - If `--hours N` specified, use N hours lookback
   - If `--max N` specified, limit to N entries  
   - If `--session all` specified, show all sessions
   - Default: last 24 hours, max 20 entries, current session only

2. **Execute History Command**: Run the agent history script
   ```
   python .claude/scripts/get-agent-history.py --time-window-hours [HOURS] --max-entries [MAX] --session-filter [SESSION]
   ```

3. **Format Output**: Present the results in a clean, readable format showing:
   - Session information
   - Timestamps of activities
   - Operation types
   - User prompts and agent results
   - File operations and context

4. **Provide Context**: Explain what activities have been happening automatically in the background

Use this to understand recent context and continue work seamlessly.
