---
name: enable-logging
description: Enable automatic Claude Code activity logging with hooks
---

# Enable Automatic Activity Logging

Enable automatic logging of all Claude Code tool operations and user prompts.

## What This Does

This command:
1. **Enables logging** in the configuration
2. **Verifies hook scripts** are present and executable
3. **Creates log directory** if it doesn't exist
4. **Confirms activation** and shows where logs are stored

## How It Works

The logging system uses Claude Code hooks to capture:
- **Tool Operations**: Read, Write, Edit, Bash, Task, Grep, Glob, etc.
- **User Prompts**: Your input to provide context
- **Session Tracking**: Links all activities to session IDs
- **Daily Rotation**: Logs organized by date

## Configuration

Logs are written to: `.claude/logs/agent-activity-YYYY-MM-DD.log`

Configuration file: `.claude/hooks/auto-logger-config.json`

## Enable Logging

Run this command to enable logging. I'll:

1. Check if hooks directory exists
2. Update configuration to enable logging
3. Verify Python scripts are executable
4. Create logs directory
5. Show you the configuration status

Let me enable automatic logging:

```bash
# Create logs directory
mkdir -p .claude/logs

# Update configuration
python3 << 'PYTHON_EOF'
import json
from pathlib import Path

config_path = Path(".claude/hooks/auto-logger-config.json")

if config_path.exists():
    with open(config_path, 'r') as f:
        config = json.load(f)
else:
    config = {}

# Enable logging
config["enabled"] = True
config["log_tool_operations"] = True
config["log_user_prompts"] = True

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print("✅ Automatic logging enabled!")
print(f"📁 Logs location: .claude/logs/")
print(f"⚙️  Config: {config_path}")
print("\nConfiguration:")
print(json.dumps(config, indent=2))
PYTHON_EOF
```

## Next Steps

**View Your Logs:**
```bash
/view-logs
```

**Configure Logging:**
```bash
/logging-config
```

**Disable Logging:**
```bash
/disable-logging
```

## What Gets Logged

Each log entry includes:
- **Timestamp**: When the operation occurred
- **Operation**: Type of tool used (read, write, bash, etc.)
- **Prompt**: Description of what was done
- **Session ID**: Links activities together
- **Context**: Additional metadata

## Notes

- Logs are in **JSON format** (one line per entry)
- Daily log rotation (new file each day)
- Session metadata tracked separately
- Errors logged to `.claude/logs/hook_errors.log`
- Minimal performance impact

**Logging is now enabled!** All tool operations will be automatically logged.
