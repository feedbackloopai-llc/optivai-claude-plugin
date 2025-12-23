---
name: logging-config
description: Configure automatic activity logging settings
---

# Configure Activity Logging

View and modify automatic logging configuration settings.

## Arguments

- `$ARGUMENTS` - Optional: `--set KEY VALUE` to update a setting, or empty to view current config

## Usage Examples

```bash
# View current configuration
/logging-config

# Enable tool operation logging
/logging-config --set log_tool_operations true

# Disable user prompt logging
/logging-config --set log_user_prompts false

# Change log level
/logging-config --set log_level debug

# Adjust max prompt length
/logging-config --set max_prompt_length 1000
```

## Configuration

```bash
python3 << 'PYTHON_EOF'
import json
import sys
from pathlib import Path

config_path = Path(".claude/hooks/auto-logger-config.json")

if not config_path.exists():
    print("❌ Configuration file not found.")
    print("Run: /enable-logging")
    sys.exit(1)

# Read current configuration
with open(config_path, 'r') as f:
    config = json.load(f)

# Parse arguments
args = "$ARGUMENTS".strip().split()

if args and len(args) >= 3 and args[0] == "--set":
    # Update configuration
    key = args[1]
    value_str = args[2]

    # Convert value to appropriate type
    if value_str.lower() == 'true':
        value = True
    elif value_str.lower() == 'false':
        value = False
    elif value_str.isdigit():
        value = int(value_str)
    else:
        value = value_str

    # Update config
    config[key] = value

    # Write back
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    print(f"✅ Updated configuration: {key} = {value}\n")

# Display current configuration
print("⚙️  Current Logging Configuration")
print("=" * 60)
print(f"\n📁 Config file: {config_path}\n")

# Core settings
print("🎯 Core Settings:")
print(f"  enabled: {config.get('enabled', True)}")
print(f"  log_level: {config.get('log_level', 'info')}")
print(f"  log_format: {config.get('log_format', 'json')}")
print(f"  session_tracking: {config.get('session_tracking', True)}")

# What to log
print("\n📝 What to Log:")
print(f"  log_tool_operations: {config.get('log_tool_operations', True)}")
print(f"  log_user_prompts: {config.get('log_user_prompts', True)}")
print(f"  log_file_operations: {config.get('log_file_operations', True)}")
print(f"  log_command_executions: {config.get('log_command_executions', True)}")
print(f"  log_agent_responses: {config.get('log_agent_responses', True)}")

# Limits and filtering
print("\n🔧 Limits & Filtering:")
print(f"  max_prompt_length: {config.get('max_prompt_length', 500)}")
print(f"  batch_log_interval: {config.get('batch_log_interval', 5)}s")

if 'smart_filtering' in config:
    sf = config['smart_filtering']
    print(f"  smart_filtering:")
    print(f"    enabled: {sf.get('enabled', True)}")
    print(f"    ignore_rapid_changes: {sf.get('ignore_rapid_changes', True)}")
    print(f"    min_operation_interval: {sf.get('min_operation_interval', 1)}s")

# Watched directories
print("\n📂 Watched Directories:")
for dir in config.get('watch_directories', []):
    print(f"  - {dir}")

# Ignore patterns
print("\n🚫 Ignore Patterns:")
for pattern in config.get('ignore_patterns', [])[:5]:
    print(f"  - {pattern}")
if len(config.get('ignore_patterns', [])) > 5:
    print(f"  ... and {len(config.get('ignore_patterns', [])) - 5} more")

print("\n" + "=" * 60)

PYTHON_EOF
```

## Available Settings

### Core Settings
- `enabled` (boolean): Enable/disable all logging
- `log_level` (string): info, debug, warning, error
- `log_format` (string): json or text
- `session_tracking` (boolean): Track session metadata

### What to Log
- `log_tool_operations` (boolean): Log tool usage (Read, Write, etc.)
- `log_user_prompts` (boolean): Log user inputs
- `log_file_operations` (boolean): Log file changes
- `log_command_executions` (boolean): Log bash commands
- `log_agent_responses` (boolean): Log agent outputs

### Limits & Filtering
- `max_prompt_length` (integer): Maximum prompt length to log
- `batch_log_interval` (integer): Seconds between batch writes
- `smart_filtering.enabled` (boolean): Enable smart filtering
- `smart_filtering.ignore_rapid_changes` (boolean): Ignore rapid file changes
- `smart_filtering.min_operation_interval` (integer): Min seconds between ops

### Directories & Patterns
- `watch_directories` (array): Directories to monitor
- `ignore_patterns` (array): Patterns to ignore

## Examples

**Reduce log verbosity:**
```bash
/logging-config --set log_file_operations false
/logging-config --set max_prompt_length 200
```

**Debug mode:**
```bash
/logging-config --set log_level debug
```

**Minimal logging:**
```bash
/logging-config --set log_tool_operations true
/logging-config --set log_user_prompts false
/logging-config --set log_file_operations false
```

## Edit Configuration Directly

```bash
# Edit in your preferred editor
code .claude/hooks/auto-logger-config.json

# Or vim
vim .claude/hooks/auto-logger-config.json
```

## View Logs

```bash
/view-logs
```

## Reset to Defaults

```bash
rm .claude/hooks/auto-logger-config.json
/enable-logging  # Will recreate with defaults
```
