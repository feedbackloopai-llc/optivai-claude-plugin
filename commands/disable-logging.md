---
name: disable-logging
description: Disable automatic Claude Code activity logging
---

# Disable Automatic Activity Logging

Temporarily disable automatic logging while keeping all configuration and logs intact.

## What This Does

This command:
1. **Disables logging** in the configuration
2. **Preserves all existing logs** (nothing is deleted)
3. **Keeps hook scripts** in place
4. **Confirms deactivation** status

## Important Notes

- ✅ **All existing logs are preserved**
- ✅ **Hook scripts remain in place**
- ✅ **Configuration is kept**
- ✅ **Can be re-enabled anytime** with `/enable-logging`

## Disable Logging

Run this to disable automatic logging:

```bash
python3 << 'PYTHON_EOF'
import json
from pathlib import Path

config_path = Path(".claude/hooks/auto-logger-config.json")

if config_path.exists():
    with open(config_path, 'r') as f:
        config = json.load(f)
else:
    print("❌ Configuration file not found. Logging may not be installed.")
    exit(1)

# Disable logging
config["enabled"] = False

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print("✅ Automatic logging disabled!")
print("📁 Existing logs preserved in: .claude/logs/")
print("\nTo re-enable logging, run: /enable-logging")
PYTHON_EOF
```

## Re-Enable Logging

To turn logging back on:

```bash
/enable-logging
```

## View Existing Logs

Your existing logs remain available:

```bash
/view-logs
```

## Completely Remove Logging

If you want to completely remove the logging system (not just disable it):

```bash
# Remove hook scripts
rm -rf .claude/hooks/

# Remove logs (optional - careful!)
# rm -rf .claude/logs/

echo "Logging system removed"
```

**Note:** This will require reinstalling hooks if you want to use logging again later.

## What Happens When Disabled

- ❌ No tool operations are logged
- ❌ No user prompts are logged
- ✅ Hook scripts still run but exit immediately
- ✅ No performance overhead
- ✅ All existing logs remain intact

**Logging has been disabled.** Re-enable anytime with `/enable-logging`.
