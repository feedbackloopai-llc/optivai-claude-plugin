# Config Logs - Manage PostgreSQL Logging Configuration

**Purpose**: View and modify the PostgreSQL activity logging configuration
**Usage**: `/config-logs [--show | --enable | --disable | --set KEY VALUE]`

## View Current Configuration

```bash
python3 <<'PYTHON_EOF'
import json
import sys
from pathlib import Path

config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"

if not config_file.exists():
    print("Configuration file not found!")
    print(f"Expected: {config_file}")
    print("")
    print("To create configuration, copy the example template:")
    print("  cp config/auto-logger-config.example.json ~/.claude/hooks/auto-logger-config.json")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = json.load(f)

print("=== PostgreSQL Logging Configuration ===")
print(f"Config file: {config_file}")
print("")

# Logging settings
logging = config.get('logging', {})
print("📝 Local Logging:")
print(f"   Enabled: {logging.get('enabled', True)}")
print(f"   Log Directory: {logging.get('log_dir', '~/.claude/logs')}")
print(f"   Session Tracking: {logging.get('session_tracking', True)}")
print("")

# PostgreSQL settings
sf = config.get('destinations', {}).get('postgresql', {})
print("❄️  PostgreSQL Sync:")
print(f"   Enabled: {sf.get('enabled', False)}")
if sf.get('enabled'):
    print(f"   Account: {sf.get('account', 'Not set')}")
    print(f"   User: {sf.get('auth', {}).get('user', 'Not set')}")
    print(f"   Warehouse: {sf.get('warehouse', 'Not set')}")
    print(f"   Target Table: {sf.get('target_table', 'Not set')}")
    print(f"   Tenant ID: {sf.get('tenant_id', 'CLAUDE_CODE')}")
    print(f"   Source System: {sf.get('source_system', 'CLAUDE_CODE')}")
    print("")

    # Sync settings
    sync = sf.get('sync', {})
    print("🔄 Sync Settings:")
    print(f"   Batch Size: {sync.get('batch_size', 100)}")
    print(f"   Flush Interval: {sync.get('flush_interval_seconds', 60)}s")
    print(f"   Retry Attempts: {sync.get('retry_attempts', 3)}")
    print(f"   Retry Delay: {sync.get('retry_delay_seconds', 5)}s")

print("")
print("=== End Configuration ===")
PYTHON_EOF
```

## Enable/Disable PostgreSQL Sync

### Enable PostgreSQL Sync
```bash
python3 <<'PYTHON_EOF'
import json
from pathlib import Path

config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
with open(config_file, 'r') as f:
    config = json.load(f)

config.setdefault('destinations', {}).setdefault('postgresql', {})['enabled'] = True

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("✓ PostgreSQL sync ENABLED")
PYTHON_EOF
```

### Disable PostgreSQL Sync
```bash
python3 <<'PYTHON_EOF'
import json
from pathlib import Path

config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
with open(config_file, 'r') as f:
    config = json.load(f)

config.setdefault('destinations', {}).setdefault('postgresql', {})['enabled'] = False

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("✓ PostgreSQL sync DISABLED")
PYTHON_EOF
```

## Configuration Reference

| Setting | Path | Description |
|---------|------|-------------|
| `logging.enabled` | Root | Enable local file logging |
| `logging.log_dir` | Root | Directory for local logs |
| `destinations.postgresql.enabled` | PostgreSQL | Enable sync to PostgreSQL |
| `destinations.postgresql.account` | PostgreSQL | PostgreSQL account identifier |
| `destinations.postgresql.target_table` | PostgreSQL | Target table (e.g., YOUR_DW_SCHEMA.LANDING.RAW_EVENTS) |
| `destinations.postgresql.tenant_id` | PostgreSQL | Tenant identifier in The Well |
| `destinations.postgresql.sync.batch_size` | Sync | Events per batch |
| `destinations.postgresql.sync.flush_interval_seconds` | Sync | Seconds between sync passes |

## Related Commands

- `/sync-status` - Check current sync status
- `/sync-now` - Manually trigger a sync
- `/db-connect` - Test database connections
