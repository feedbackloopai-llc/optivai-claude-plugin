# Configure Context Bundle Logging

View and modify the automatic logging configuration settings.

## Arguments

- `$ARGUMENTS` - Optional config operation: `--set KEY VALUE`, `--get KEY`, or no args to view all

## Instructions

1. **Parse Configuration Request**: Determine the operation
   - If `--set KEY VALUE`: Update a configuration setting
   - If `--get KEY`: Retrieve a specific setting
   - If no arguments: Display all current settings

2. **Execute Configuration Command**: Run the config command
   ```
   python .claude/commands/activity_commands.py log-config [ARGS]
   ```

3. **Display Configuration**: Show current settings with explanations:
   - **default_time_window**: Default hours for history lookback
   - **default_max_entries**: Default maximum entries to display
   - **auto_log_commands**: Whether to auto-log Claude Code commands
   - **output_format**: Preferred output format for displays

4. **Explain Settings**: For each setting, provide:
   - Current value
   - What it controls
   - Recommended values
   - Impact of changes

5. **Confirm Changes**: If modifying settings, confirm the new values and their effects

Perfect for customizing the automatic context bundle logging behavior.
