# Sync Now - Manual PostgreSQL Sync

**Purpose**: Manually trigger a single sync pass to PostgreSQL's "The Well"
**Usage**: `/sync-now`

## Trigger Manual Sync

```bash
# Get the plugin directory (where pg_sync.py lives)
PLUGIN_DIR="${HOME}/Documents/optivai/optivai-claude-plugin"

if [ ! -f "${PLUGIN_DIR}/scripts/pg_sync.py" ]; then
    # Try alternate location
    PLUGIN_DIR="$(dirname "$(dirname "$(realpath ~/.claude/hooks/pre-tool-use.py 2>/dev/null || echo '')")")"
fi

if [ -f "${PLUGIN_DIR}/scripts/pg_sync.py" ]; then
    echo "=== Manual PostgreSQL Sync ==="
    echo "Running single sync pass..."
    echo ""
    python3 "${PLUGIN_DIR}/scripts/pg_sync.py" --once
    echo ""
    echo "=== Sync Complete ==="
else
    echo "Error: pg_sync.py not found"
    echo "Expected location: ${PLUGIN_DIR}/scripts/pg_sync.py"
    echo ""
    echo "Make sure the optivai-claude-plugin is installed."
fi
```

## Dry Run (Preview Only)

To preview what would be synced without actually writing to PostgreSQL:

```bash
PLUGIN_DIR="${HOME}/Documents/optivai/optivai-claude-plugin"
python3 "${PLUGIN_DIR}/scripts/pg_sync.py" --dry-run
```

## Start Background Daemon

To start continuous sync as a background daemon:

```bash
PLUGIN_DIR="${HOME}/Documents/optivai/optivai-claude-plugin"
python3 "${PLUGIN_DIR}/scripts/pg_sync.py" --daemon
```

## Related Commands

- `/sync-status` - Check sync status
- `/activity-query` - Query activity from PostgreSQL
