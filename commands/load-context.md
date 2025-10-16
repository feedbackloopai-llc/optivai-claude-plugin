# Load Context Bundle Data

Load and display context bundle information for a specified time period to get the agent up to speed.

## Arguments

- `$ARGUMENTS` - Optional time specification: `--days 5`, `--hours 24`, `--all`

## Instructions

1. **Determine Time Scope**: Parse the time specification
   - If `--days N`: Load last N days of context
   - If `--hours N`: Load last N hours of context  
   - If `--all`: Load all available context (use carefully)
   - Default: Last 24 hours

2. **Load Context Data**: Execute history command with appropriate filters
   ```
   python .claude/scripts/get-agent-history.py --time-window-hours [CALCULATED_HOURS] --max-entries 50 --session-filter all
   ```

3. **Provide Comprehensive Context**: Present the loaded information as:
   - **Recent Activities**: What has been happening
   - **Current State**: Files modified, features worked on
   - **Context Threads**: Ongoing work streams and their status
   - **Blockers/Issues**: Any problems encountered recently

4. **Suggest Continuation**: Based on loaded context, recommend where to pick up work

5. **Update Agent Understanding**: Explicitly state that you now have full context of recent activities and can continue seamlessly

Perfect for onboarding an agent with full context of recent development activities.
