# Start New Session

Start a new session with a fresh session ID while preserving the auto-logging system.

## Instructions

1. **Execute Session Reset**: Run the new session command
   ```
   python .claude/commands/activity_commands.py clear-session
   ```

2. **Confirm New Session**: Display the new session information:
   - Previous session ID
   - New session ID  
   - Transition timestamp

3. **Explain Impact**: Clarify what happens with the session change:
   - Auto-logging continues with new session ID
   - Previous session data remains available for searching
   - Context bundles will now track new session activities
   - Fresh start for current work tracking

4. **Log Transition**: Ensure the session change is properly logged for continuity

Perfect for starting fresh while maintaining historical context accessibility.
