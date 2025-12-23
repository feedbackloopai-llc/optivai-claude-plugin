# New Session - Start Fresh Session with New ID

**Purpose**: Start a new session with a fresh session ID for activity tracking
**Usage**: `/new-session`

Use this to explicitly mark session boundaries when starting new work streams.

## Start New Session

```bash
python3 <<'PYTHON_EOF'
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

# Session state file
state_file = Path.home() / ".claude" / "logs" / ".session_state.json"
state_file.parent.mkdir(parents=True, exist_ok=True)

# Load current state
old_session_id = None
if state_file.exists():
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
            old_session_id = state.get('session_id')
    except:
        pass

# Generate new session ID
timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
unique_id = uuid.uuid4().hex[:8]
new_session_id = f"session-{timestamp}-{unique_id}"

# Save new session state
new_state = {
    'session_id': new_session_id,
    'started_at': datetime.now(timezone.utc).isoformat(),
    'previous_session': old_session_id
}

with open(state_file, 'w') as f:
    json.dump(new_state, f, indent=2)

print("")
print("=" * 50)
print("  NEW SESSION STARTED")
print("=" * 50)
print("")
if old_session_id:
    print(f"  Previous Session: {old_session_id}")
print(f"  New Session ID:   {new_session_id}")
print(f"  Started At:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("")
print("  Activity logging will now use the new session ID.")
print("  Previous session data remains accessible for queries.")
print("")
print("=" * 50)

# Log session transition to local log
log_dir = Path.home() / ".claude" / "logs"
today = datetime.now().strftime("%Y-%m-%d")
log_file = log_dir / f"agent-activity-{today}.log"

transition_event = {
    "epoch": int(datetime.now().timestamp()),
    "date": today,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "time": datetime.now().strftime("%H:%M:%S"),
    "operation": "session_transition",
    "prompt": f"New session started: {new_session_id}",
    "session_id": new_session_id,
    "user": Path.home().name,
    "cwd": str(Path.cwd()),
    "project": Path.cwd().name,
    "details": {
        "previous_session": old_session_id,
        "new_session": new_session_id,
        "transition_type": "manual"
    }
}

with open(log_file, 'a') as f:
    f.write(json.dumps(transition_event) + '\n')

print("  Session transition logged.")
print("")
PYTHON_EOF
```

## When to Use

- **Starting a new task** - Clean separation from previous work
- **Switching projects** - Mark the context switch
- **After a break** - Fresh start after stepping away
- **For reporting** - Clear session boundaries for activity analysis

## What Happens

1. **New Session ID Generated** - Format: `session-YYYYMMDD-HHMMSS-xxxxxxxx`
2. **State File Updated** - `~/.claude/logs/.session_state.json`
3. **Transition Logged** - Event written to activity log
4. **Previous Data Preserved** - Old session data remains queryable

## Session ID Format

```
session-20251210-143022-a1b2c3d4
        │        │       │
        │        │       └── Random 8-char hex
        │        └── Time: HHMMSS
        └── Date: YYYYMMDD
```

## Related Commands

- `/prime-agent` - Load full context including activity logs
- `/quick-context` - Fast context loading
- `/view-logs` - View recent activity logs
