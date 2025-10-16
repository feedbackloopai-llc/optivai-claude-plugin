# Export Context Bundle Session

Export current session's auto-logged activities to a file for analysis or archival.

## Arguments

- `$ARGUMENTS` - Optional export format and file: `--format json|csv|markdown`, `--output filename`

## Instructions

1. **Parse Export Options**: Check user specifications
   - If `--format [FORMAT]`: Use specified format (json, csv, markdown)
   - If `--output [FILE]`: Use specified filename
   - Default: JSON format with auto-generated filename

2. **Execute Export**: Run the export command
   ```
   python .claude/commands/activity_commands.py export-session --format [FORMAT] --output [OUTPUT]
   ```

3. **Confirm Export**: Report successful export with:
   - File location and name
   - Number of activities exported
   - Format used
   - Session timeframe covered

4. **Suggest Usage**: Explain how the exported file can be used:
   - Analysis in other tools
   - Sharing with team members
   - Creating reports
   - Archival purposes

Perfect for preserving and analyzing session context bundle data.
