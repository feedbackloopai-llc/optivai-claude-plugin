# Sync Status - Check PostgreSQL Sync Status

**Purpose**: Check the status of activity log synchronization to PostgreSQL's "The Well"
**Usage**: `/sync-status`

## Check Sync Status

```bash
# Check sync status from the plugin's sync service
python3 <<'PYTHON_EOF'
import json
from pathlib import Path

state_file = Path.home() / ".claude" / "logs" / ".sync_state.json"

if not state_file.exists():
    print("No sync state found. Sync service may not have run yet.")
    print(f"Expected state file: {state_file}")
    exit(0)

with open(state_file, 'r') as f:
    state = json.load(f)

print("=== PostgreSQL Sync Status ===\n")
print(f"Total Events Synced: {state.get('total_synced', 0)}")
print(f"Last Sync Time: {state.get('last_sync_time', 'Never')}")
print(f"Projects Tracked: {len(state.get('projects', {}))}")

projects = state.get('projects', {})
if projects:
    print("\nProject Sync Details:")
    for path, info in projects.items():
        project_name = Path(path).name
        last_sync = info.get('last_sync', 'Never')
        last_file = Path(info.get('last_file', '')).name if info.get('last_file') else 'None'
        print(f"  - {project_name}:")
        print(f"      Last Sync: {last_sync}")
        print(f"      Last File: {last_file}")
else:
    print("\nNo projects have been synced yet.")

# Check config status
config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
if config_file.exists():
    with open(config_file, 'r') as f:
        config = json.load(f)
    sf_config = config.get('destinations', {}).get('postgresql', {})
    enabled = sf_config.get('enabled', False)
    print(f"\nPostgreSQL Sync Enabled: {'Yes' if enabled else 'No'}")
    if enabled:
        print(f"Target Table: {sf_config.get('target_table', 'Not configured')}")
else:
    print(f"\nConfig file not found: {config_file}")
    print("Run setup to configure PostgreSQL sync.")

print("\n=== End Status ===")
PYTHON_EOF
```

## Related Commands

- `/sync-now` - Manually trigger a sync
- `/activity-query` - Query activity from PostgreSQL
- `/history` - View local activity history
