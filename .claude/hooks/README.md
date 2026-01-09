# Automatic Activity Logging System

A comprehensive hook-based logging system for Claude Code that automatically captures all tool operations, user prompts, and agent activities.

## Overview

This system uses **Claude Code hooks** to automatically log every interaction without requiring manual logging calls. It's perfect for:

- **Activity Tracking**: See exactly what Claude Code did
- **Debugging**: Trace operations when things go wrong
- **Auditing**: Maintain records of all activities
- **Context Building**: Understand workflows and patterns
- **Session Analytics**: Analyze usage patterns

## Architecture

### Components

```
.claude/hooks/
├── pre-tool-use.py          # Captures tool operations (Read, Write, Bash, etc.)
├── user-prompt-submit.py    # Captures user prompts for context
├── log-writer.py            # Core logging engine (activity logging)
├── memory_writer.py         # Enhanced memory system with backup/recovery
├── aggregate_hook_logs.py   # Recovery script for work_log.yaml
├── setup-ralph-loop.sh      # Ralph Wiggum Loop setup script
├── stop-hook.sh             # Ralph Wiggum Loop stop hook
├── auto-logger-config.json  # Configuration file
├── settings-template.json   # Template for project settings
└── README.md                # This file

.claude/logs/
└── agent-activity-YYYY-MM-DD.log  # Daily log files (JSON Lines format)

~/.claude/gz-observability-memory/  # Global memory system
├── session_state.yaml       # Current session context
├── planned_tasks.yaml       # Tasks synced from TodoWrite
├── work_log.yaml            # Chronological action history (500 max)
├── recovery_checkpoint.yaml # Crash recovery state
├── backups/                 # Timestamped backups before overwrites
├── archive/                 # Quarterly rolloff of old entries
└── corrupt/                 # Quarantined corrupt files
```

### How It Works

1. **User submits prompt** → `UserPromptSubmit` hook → `user-prompt-submit.py` logs it
2. **Claude uses tool** (Read, Write, etc.) → `PreToolUse` hook → `pre-tool-use.py` logs it
3. **Log writer** writes JSON entry to daily log file
4. **Session tracking** links all operations together

### Data Flow

```
User Input → UserPromptSubmit Hook → Log User Prompt
Claude Tool → PreToolUse Hook → Capture Tool Info → Log Operation
Both → log-writer.py → JSON Log File
```

## Installation

### Option 1: Install in Current Project

Use the `/install-logging-hooks` command to install hooks in your current project:

```bash
/install-logging-hooks
```

This copies all hook scripts to `.claude/hooks/` and configures `settings.local.json`.

### Option 2: Manual Installation

1. **Copy hook scripts:**
   ```bash
   cp -r /path/to/plugin/.claude/hooks .claude/
   ```

2. **Update `.claude/settings.local.json`:**
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python .claude/hooks/pre-tool-use.py"
             }
           ]
         }
       ],
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python .claude/hooks/user-prompt-submit.py"
             }
           ]
         }
       ]
     }
   }
   ```

3. **Enable logging:**
   ```bash
   /enable-logging
   ```

## Configuration

### Configuration File

Located at `.claude/hooks/auto-logger-config.json`:

```json
{
  "enabled": true,
  "log_tool_operations": true,
  "log_user_prompts": true,
  "log_level": "info",
  "max_prompt_length": 500,
  "session_tracking": true,
  "ignore_patterns": [
    "*.log", "*.tmp", "*.cache",
    ".git/*", "node_modules/*"
  ]
}
```

### Configure via Command

```bash
# View configuration
/logging-config

# Update settings
/logging-config --set log_tool_operations false
/logging-config --set max_prompt_length 1000
```

## Usage

### View Logs

```bash
# View last 20 entries
/view-logs

# View last 50 entries
/view-logs 50

# View all today's logs
/view-logs today
```

### Export Logs

```bash
# Export as JSON
/export-logs json

# Export as CSV
/export-logs csv

# Export as Markdown report
/export-logs markdown
```

### Enable/Disable

```bash
# Enable logging
/enable-logging

# Disable logging (keeps all logs)
/disable-logging
```

## Log Format

### JSON Lines Format

Each log file contains one JSON object per line:

```json
{
  "timestamp": "2025-12-09T20:15:30.123Z",
  "operation": "read",
  "prompt": "read: src/app.ts",
  "session_id": "session-20251209-201530-a1b2c3d4",
  "time": "20:15:30"
}
```

### Fields

- `timestamp`: ISO 8601 UTC timestamp
- `operation`: Type of operation (read, write, bash, user_prompt, etc.)
- `prompt`: Human-readable description
- `session_id`: Unique session identifier
- `time`: Local time (HH:MM:SS)
- `result`: (Optional) Operation result
- `context`: (Optional) Additional metadata

### Operation Types

**Tool Operations:**
- `read`: File read (Read tool)
- `write`: File write (Write tool)
- `edit`: File edit (Edit tool)
- `bash`: Bash command (Bash tool)
- `glob`: File pattern search (Glob tool)
- `grep`: Content search (Grep tool)
- `task`: Agent task (Task tool)
- `web_fetch`: Web fetch (WebFetch tool)
- `web_search`: Web search (WebSearch tool)
- `todo_write`: Todo list update (TodoWrite tool)
- `slash_command`: Slash command execution (SlashCommand tool)

**User Interactions:**
- `user_prompt`: User submitted a prompt

## Examples

### Example 1: Basic Usage

```bash
# Install hooks
/install-logging-hooks

# Enable logging
/enable-logging

# Work normally - all operations are logged automatically
# ... use Claude Code normally ...

# View what happened
/view-logs
```

### Example 2: Debugging a Session

```bash
# View all activities from current session
/view-logs today

# Export detailed report
/export-logs markdown

# Review the markdown report
cat activity-log-2025-12-09.md
```

### Example 3: Analyzing Patterns

```bash
# Export as CSV for analysis
/export-logs csv

# Import to spreadsheet or database
# Analyze tool usage patterns, time spent, etc.
```

## Advanced Usage

### Session Tracking

Each execution gets a unique session ID in the format:
```
session-YYYYMMDD-HHMMSS-shortid
```

All operations in a session are linked by this ID, allowing you to:
- Filter logs by session
- Trace complete workflows
- Analyze session duration

### Custom Analysis

```bash
# Count operations by type
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq -s 'group_by(.operation) | map({operation: .[0].operation, count: length})'

# Find all bash commands
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq 'select(.operation == "bash")'

# Get unique sessions
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq -r '.session_id' | sort -u
```

### Integration with Other Tools

The JSON Lines format makes it easy to integrate with:

- **Databases**: Import into PostgreSQL, SQLite, MongoDB
- **Analytics**: Load into Pandas, R, Excel
- **Monitoring**: Stream to Elasticsearch, Splunk
- **Visualization**: Create dashboards with Grafana, Kibana

## Performance

### Impact

- **Hook execution time**: < 5ms per operation
- **File I/O**: Append-only (very fast)
- **Memory usage**: Minimal (streaming writes)
- **CPU usage**: Negligible

### Optimization

The system is designed for minimal impact:
- Logs are written asynchronously when possible
- File operations are batched
- Smart filtering reduces noise
- Daily rotation prevents large files

## Troubleshooting

### Hooks Not Running

**Check scripts are executable:**
```bash
ls -l .claude/hooks/*.py
chmod +x .claude/hooks/*.py
```

**Check settings:**
```bash
cat .claude/settings.local.json | jq .hooks
```

### No Logs Appearing

**Check if enabled:**
```bash
cat .claude/hooks/auto-logger-config.json | jq .enabled
```

**Check for errors:**
```bash
cat .claude/logs/hook_errors.log
```

### Permission Errors

```bash
# Fix permissions
chmod 755 .claude/hooks
chmod 644 .claude/hooks/*.json
chmod +x .claude/hooks/*.py
```

### Python Not Found

```bash
# Check Python installation
which python3
python3 --version

# Update hook commands if needed
# Edit .claude/settings.local.json and change python3 to python
```

## Security & Privacy

### Local Only

- All logs stay on your machine
- Nothing is sent to external servers
- No network requests from logging system

### Sensitive Data

Be careful logging projects with sensitive information:

```bash
# Disable logging for sensitive projects
/disable-logging

# Or exclude sensitive files
/logging-config  # Edit ignore_patterns
```

### Log Retention

Logs are never automatically deleted. Manage them manually:

```bash
# Remove old logs (older than 30 days)
find .claude/logs -name "*.log" -mtime +30 -delete

# Archive logs
tar -czf logs-archive-$(date +%Y%m).tar.gz .claude/logs/
rm -rf .claude/logs/*.log
```

## Best Practices

1. **Enable for development**: Great for debugging and learning
2. **Disable for production**: Reduce overhead in production
3. **Regular exports**: Export important sessions as markdown reports
4. **Clean old logs**: Archive or delete logs periodically
5. **Review patterns**: Analyze logs to optimize workflows

## Commands Reference

| Command | Description |
|---------|-------------|
| `/install-logging-hooks` | Install hooks in current project |
| `/enable-logging` | Enable automatic logging |
| `/disable-logging` | Disable logging |
| `/view-logs [N]` | View last N log entries |
| `/logging-config` | View/update configuration |
| `/export-logs [format]` | Export logs (json, csv, markdown) |

## FAQ

**Q: Does this slow down Claude Code?**
A: No, the impact is < 5ms per operation and barely noticeable.

**Q: Where are logs stored?**
A: In `.claude/logs/agent-activity-YYYY-MM-DD.log` in your project directory.

**Q: Can I use this across all projects?**
A: Hooks are project-specific. Run `/install-logging-hooks` in each project.

**Q: Will this log sensitive data?**
A: Yes, it logs all tool operations. Use ignore_patterns to exclude sensitive files.

**Q: Can I customize what gets logged?**
A: Yes, use `/logging-config` to enable/disable specific log types.

**Q: How do I share logs?**
A: Export to markdown format and review before sharing to remove sensitive data.

## Enhanced Memory System

The memory system provides persistent storage across sessions with automatic backup and recovery.

### Memory System Features

- **Backup Before Write**: Critical files are backed up before modification
- **Automatic Recovery**: Corrupt files are quarantined and recovered from backups
- **Project Context**: Every entry includes project name and working directory
- **90-Day Lookback**: Extended history search for comprehensive context
- **File History Tracking**: Track which files were modified across sessions

### Memory Files

| File | Purpose |
|------|---------|
| `session_state.yaml` | Current session context, focus, operation count |
| `planned_tasks.yaml` | Tasks synced from TodoWrite (pending/in_progress/completed) |
| `work_log.yaml` | Chronological history of significant operations (last 500) |
| `recovery_checkpoint.yaml` | Crash recovery state |

### Recovery from Corruption

If the work_log.yaml becomes corrupted:

```bash
# Show statistics about available logs
python .claude/hooks/aggregate_hook_logs.py --stats

# Preview what would be imported
python .claude/hooks/aggregate_hook_logs.py --recover --dry-run

# Recover all available logs (creates backup first)
python .claude/hooks/aggregate_hook_logs.py --recover

# Recover specific date range
python .claude/hooks/aggregate_hook_logs.py --from-date 2026-01-05 --to-date 2026-01-06 --recover
```

### Getting Project Context

```python
from memory_writer import get_comprehensive_project_context

# Get 90 days of context for a project
context = get_comprehensive_project_context("/path/to/project")

# Includes:
# - Direct project hook log search
# - Memory system data filtered to project
# - File history tracking
# - Summary statistics
```

---

## Ralph Wiggum Loop

The Ralph Wiggum Loop is a self-referential AI development methodology for iterative tasks.

### What is Ralph?

Named after a development methodology pioneered by Geoffrey Huntley, the Ralph loop creates iterative feedback loops where:

1. You provide a task prompt with clear completion criteria
2. Claude works on the task
3. When Claude tries to exit, the Stop hook intercepts
4. The SAME prompt is fed back
5. Claude sees previous work in files and iterates
6. Loop continues until completion or max iterations

### Usage

```bash
# Start a loop with iteration limit
/ralph-loop Build a REST API --max-iterations 20

# With completion promise (recommended)
/ralph-loop Fix all tests --completion-promise 'ALL TESTS PASS' --max-iterations 30

# Cancel an active loop
/cancel-ralph
```

### When to Use Ralph

- Test-driven development (make all tests pass)
- Iterative refinement tasks (fix linting errors)
- Complex multi-step implementations
- Bug fixing loops
- Autonomous work sessions

### Completion Promise

To signal completion, output in your response:

```
<promise>YOUR_COMPLETION_PHRASE</promise>
```

**Rule**: Only output a promise when the statement is completely TRUE.

### State File

Ralph creates `.claude/ralph-loop.local.md` in your project:

```yaml
---
active: true
iteration: 5
max_iterations: 20
completion_promise: "ALL TESTS PASS"
started_at: "2026-01-09T10:00:00Z"
---

Fix all failing tests. Output <promise>ALL TESTS PASS</promise> when done.
```

### Settings Configuration

The Stop hook must be configured in `settings.local.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/stop-hook.sh"
          }
        ]
      }
    ]
  }
}
```

---

## Contributing

Found a bug or want a feature? This is part of the optivai-claude-plugin.

## License

MIT License - Part of the optivai-claude-plugin
