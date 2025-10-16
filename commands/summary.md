# Session Activity Summary

Generate a comprehensive summary of the current session's auto-logged activities.

## Instructions

1. **Get Current Session Data**: Execute the session summary command
   ```
   python .claude/commands/activity_commands.py session-summary
   ```

2. **Analyze Activity Patterns**: Review the session data and identify:
   - Total number of activities logged
   - Most common operations performed
   - Duration of the session
   - Files created, modified, or deleted
   - Commands executed
   - Key workflows completed

3. **Present Executive Summary**: Format as a brief, actionable summary:
   - **Session Overview**: Duration, total activities, session ID
   - **Key Operations**: Top 3-5 operation types with counts
   - **File Activity**: Notable file operations
   - **Progress Made**: What was accomplished this session

4. **Suggest Next Steps**: Based on recent activity, suggest logical next actions

Perfect for understanding what's been happening automatically in your development session.
