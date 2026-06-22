---
name: install-logging-hooks
description: Install automatic activity logging hooks in current project
---

# Install Activity Logging Hooks

Install the automatic activity logging system in your current project by copying hook scripts and configuring Claude Code hooks.

## What This Does

This command will:
1. **Copy hook scripts** to `.claude/hooks/` in current project
2. **Copy configuration** to `.claude/hooks/auto-logger-config.json`
3. **Update settings** to enable hooks in `.claude/settings.local.json`
4. **Create logs directory** at `.claude/logs/`
5. **Verify installation** and show you how to test it

## Installation

Let me install the automatic logging hooks:

```bash
# Get plugin installation path
PLUGIN_PATH=$(python3 -c "
import os
from pathlib import Path

# Common plugin paths to check
paths_to_check = [
    Path.home() / '.claude' / 'plugins' / 'chris-claude-toolkit',
    Path.home() / '.claude' / 'plugins' / 'optivai-claude-plugin',
    Path('C:/Users/chris/Documents/optivai-claude-plugin')
]

for path in paths_to_check:
    if path.exists() and (path / '.claude' / 'hooks').exists():
        print(str(path))
        exit(0)

# Fallback
print('')
")

if [ -z "$PLUGIN_PATH" ]; then
    echo "❌ Could not find plugin installation path"
    echo "Please install the optivai-claude-plugin first"
    echo "Run: /plugin install file:///c:/Users/chris/Documents/optivai-claude-plugin"
    exit 1
fi

echo "📦 Found plugin at: $PLUGIN_PATH"

# Create local .claude directory structure
mkdir -p .claude/hooks
mkdir -p .claude/logs

# Copy hook scripts
echo "📋 Copying hook scripts..."
cp "$PLUGIN_PATH/.claude/hooks/pre-tool-use.py" .claude/hooks/
cp "$PLUGIN_PATH/.claude/hooks/user-prompt-submit.py" .claude/hooks/
cp "$PLUGIN_PATH/.claude/hooks/log-writer.py" .claude/hooks/
cp "$PLUGIN_PATH/.claude/hooks/auto-logger-config.json" .claude/hooks/

# Make scripts executable
chmod +x .claude/hooks/pre-tool-use.py
chmod +x .claude/hooks/user-prompt-submit.py
chmod +x .claude/hooks/log-writer.py

echo "✅ Hook scripts installed"

# Update or create settings.local.json
python3 << 'PYTHON_EOF'
import json
from pathlib import Path

settings_file = Path(".claude/settings.local.json")

# Read existing settings or create new
if settings_file.exists():
    with open(settings_file, 'r') as f:
        settings = json.load(f)
else:
    settings = {
        "$schema": "https://json.schemastore.org/claude-code-settings.json"
    }

# Add hooks configuration
settings["hooks"] = {
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

# Write back
with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)

print(f"✅ Updated {settings_file}")
PYTHON_EOF

echo ""
echo "=" * 80
echo "✅ Automatic Activity Logging Installed!"
echo "=" * 80
echo ""
echo "📁 Hook scripts: .claude/hooks/"
echo "📝 Configuration: .claude/hooks/auto-logger-config.json"
echo "💾 Logs directory: .claude/logs/"
echo "⚙️  Settings: .claude/settings.local.json"
echo ""
echo "🔍 Test the installation:"
echo "  1. Run any command (e.g., /history)"
echo "  2. Check logs: /view-logs"
echo ""
echo "⚙️  Configure logging:"
echo "  /logging-config"
echo ""
echo "🚫 Disable logging:"
echo "  /disable-logging"
echo ""
```

## Verify Installation

Check that everything is installed correctly:

```bash
# Verify hook scripts exist
ls -lh .claude/hooks/

# Verify configuration
cat .claude/hooks/auto-logger-config.json | jq .

# Verify settings
cat .claude/settings.local.json | jq .hooks

# Check if logging is enabled
python3 -c "import json; print('Enabled:', json.load(open('.claude/hooks/auto-logger-config.json'))['enabled'])"
```

## Test Logging

Test that logging is working:

```bash
# Enable logging
/enable-logging

# Run a simple command to trigger logging
echo "test" > test-file.txt

# View the logs
/view-logs

# Clean up
rm test-file.txt
```

## What Gets Installed

### Hook Scripts (.claude/hooks/)
- `pre-tool-use.py` - Captures tool operations before execution
- `user-prompt-submit.py` - Captures user prompts for context
- `log-writer.py` - Core logging functionality

### Configuration
- `auto-logger-config.json` - Logging behavior configuration

### Settings
- `settings.local.json` - Hooks configuration for Claude Code

### Logs Directory
- `.claude/logs/` - Where activity logs are written

## Customization

After installation, customize the logging behavior:

```bash
# Configure what to log
/logging-config --set log_file_operations false

# Change log level
/logging-config --set log_level debug

# Adjust verbosity
/logging-config --set max_prompt_length 1000
```

## Uninstall

To remove the logging system:

```bash
# Remove hook scripts
rm -rf .claude/hooks/

# Remove settings (careful - this removes ALL local settings)
# rm .claude/settings.local.json

# Optionally remove logs
# rm -rf .claude/logs/
```

## Notes

- Hooks are **project-specific** (installed per project)
- Logging runs automatically once installed
- Minimal performance impact
- Logs rotate daily
- All data stays local (never sent anywhere)

## Troubleshooting

**Hooks not running:**
```bash
# Check if scripts are executable
ls -l .claude/hooks/*.py

# Make executable
chmod +x .claude/hooks/*.py

# Check Python path
which python3
```

**No logs appearing:**
```bash
# Check if logging is enabled
cat .claude/hooks/auto-logger-config.json | grep enabled

# Enable logging
/enable-logging

# Check for errors
cat .claude/logs/hook_errors.log
```

**Permission errors:**
```bash
# Fix permissions
chmod 755 .claude/hooks
chmod +x .claude/hooks/*.py
```

The automatic activity logging system is now installed and ready to use!
