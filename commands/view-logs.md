---
name: view-logs
description: View recent Claude Code activity logs
---

# View Activity Logs

View recent Claude Code tool operations and user prompts from the activity log.

## Arguments

- `$ARGUMENTS` - Optional: number of entries to show (default: 20) or 'today' for all today's logs

## Usage Examples

```bash
# View last 20 entries (default)
/view-logs

# View last 50 entries
/view-logs 50

# View all entries from today
/view-logs today

# View all entries
/view-logs all
```

## View Logs

Let me retrieve and display your recent activity logs:

```bash
python3 << 'PYTHON_EOF'
import json
import sys
from pathlib import Path
from datetime import datetime

# Parse arguments
args = "$ARGUMENTS".strip().split()
limit = 20  # default

if args and args[0]:
    arg = args[0].lower()
    if arg == "today":
        limit = None  # all today
    elif arg == "all":
        limit = 999999
    else:
        try:
            limit = int(arg)
        except ValueError:
            limit = 20

# Find log file
log_dir = Path(".claude/logs")
if not log_dir.exists():
    print("❌ No logs directory found. Logging may not be enabled.")
    print("Run: /enable-logging")
    sys.exit(1)

# Get today's log file
date = datetime.now().strftime("%Y-%m-%d")
log_file = log_dir / f"agent-activity-{date}.log"

if not log_file.exists():
    print(f"❌ No log file found for today: {log_file}")
    print("\nAvailable log files:")
    for f in sorted(log_dir.glob("agent-activity-*.log")):
        print(f"  - {f.name}")
    sys.exit(0)

# Read log entries
entries = []
with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and line.startswith('{'):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

if not entries:
    print(f"📝 No log entries found in {log_file.name}")
    sys.exit(0)

# Display entries
if limit and len(entries) > limit:
    entries = entries[-limit:]

print(f"\n📊 Activity Log: {log_file.name}")
print(f"📝 Showing {len(entries)} entries\n")
print("=" * 80)

for entry in entries:
    time = entry.get('time', 'N/A')
    operation = entry.get('operation', 'unknown')
    prompt = entry.get('prompt', '')
    session_id = entry.get('session_id', 'N/A')

    # Truncate long prompts for display
    if len(prompt) > 100:
        prompt = prompt[:100] + "..."

    print(f"\n[{time}] {operation.upper()}")
    print(f"  → {prompt}")
    print(f"  Session: {session_id[:12]}...")

    if 'result' in entry:
        result = entry['result']
        if len(result) > 80:
            result = result[:80] + "..."
        print(f"  Result: {result}")

print("\n" + "=" * 80)
print(f"\n💾 Full log: {log_file}")
print(f"📊 Total entries today: {len(entries)}")

# Show session info if available
unique_sessions = set(e.get('session_id', '') for e in entries)
print(f"🔗 Active sessions: {len(unique_sessions)}")

PYTHON_EOF
```

## Log File Locations

View log files directly:

```bash
# List all log files
ls -lh .claude/logs/

# View today's log file
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq .

# Count total entries
wc -l .claude/logs/agent-activity-*.log
```

## Export Logs

To export logs for analysis:

```bash
/export-logs
```

## Configuration

View or modify logging configuration:

```bash
/logging-config
```

## Log Entry Format

Each log entry contains:
- `timestamp`: ISO 8601 UTC timestamp
- `operation`: Type of operation (read, write, bash, etc.)
- `prompt`: Description of what was done
- `session_id`: Session identifier
- `time`: Local time (HH:MM:SS)
- `result`: Optional result/outcome
- `context`: Optional additional metadata

## Notes

- Logs rotate daily (new file per day)
- JSON format (one entry per line)
- Session IDs link related operations
- Old logs are never automatically deleted
