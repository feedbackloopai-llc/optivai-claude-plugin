# Load Context - Load Historical Activity from PostgreSQL

**Purpose**: Load and summarize historical activity from PostgreSQL to understand recent work over multiple days
**Usage**: `/load-context [--days N]` (default: 3 days)

Use this when returning after time away to understand what work has been done recently.

## Load Historical Context

```bash
python3 <<'PYTHON_EOF'
import sys
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Parse arguments
days = 3
for i, arg in enumerate(sys.argv[1:]):
    if arg == '--days' and i + 2 < len(sys.argv):
        try:
            days = int(sys.argv[i + 2])
        except ValueError:
            pass

hours = days * 24

# Load PostgreSQL config
config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
if not config_file.exists():
    print("Error: PostgreSQL configuration not found")
    print("Run /config-logs for setup instructions")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = json.load(f)

sf_config = config.get('destinations', {}).get('postgresql', {})
if not sf_config.get('enabled'):
    print("PostgreSQL sync not enabled. Showing local logs instead...")
    print("")
    # Fall back to local logs
    log_dir = Path.home() / ".claude" / "logs"
    if log_dir.exists():
        log_files = sorted(log_dir.glob("agent-activity-*.log"), reverse=True)[:days]
        for lf in log_files:
            print(f"  - {lf.name}")
    sys.exit(0)

try:
    import psycopg2.connector
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Error: Required packages not installed")
    print("Run: pip install psycopg2-binary cryptography")
    sys.exit(1)

# Connect to PostgreSQL
key_path = Path(sf_config['auth']['private_key_path']).expanduser()
with open(key_path, 'rb') as f:
    key_data = f.read()
try:
    private_key = serialization.load_der_private_key(key_data, password=None, backend=default_backend())
except Exception:
    private_key = serialization.load_pem_private_key(key_data, password=None, backend=default_backend())
pkb = private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

conn = psycopg2.connect(
    account=sf_config['account'],
    user=sf_config['auth']['user'],
    private_key=pkb,
    warehouse=sf_config['warehouse'],
    role=sf_config.get('role', 'ACCOUNTADMIN')
)

table = sf_config.get('target_table', 'DW_DEV_STREAM.LANDING.RAW_EVENTS')
tenant_id = sf_config.get('tenant_id', 'CLAUDE_CODE')

# Query activity summary
query = f"""
SELECT
    DATE(EVENT_AT) as activity_date,
    SUBJECT_ID as project,
    METADATA:operation::STRING as operation,
    METADATA:prompt::STRING as prompt,
    METADATA:session_id::STRING as session_id,
    EVENT_AT
FROM {table}
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND TENANT_ID = '{tenant_id}'
  AND EVENT_AT > DATEADD(day, -{days}, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC
"""

cursor = conn.cursor()
cursor.execute(query)
rows = cursor.fetchall()

print(f"")
print(f"=" * 60)
print(f"  CONTEXT BRIEFING - Last {days} Days of Activity")
print(f"=" * 60)
print(f"")

if not rows:
    print("No activity found in the specified time window.")
    cursor.close()
    conn.close()
    sys.exit(0)

# Aggregate by date and project
by_date = defaultdict(lambda: defaultdict(list))
all_sessions = set()
all_projects = set()
user_prompts = []

for row in rows:
    date, project, operation, prompt, session_id, event_at = row
    by_date[str(date)][project].append({
        'operation': operation,
        'prompt': prompt,
        'session_id': session_id,
        'event_at': event_at
    })
    if session_id:
        all_sessions.add(session_id)
    if project:
        all_projects.add(project)
    if operation == 'user_prompt' and prompt:
        user_prompts.append({'date': str(date), 'prompt': prompt[:150], 'project': project})

# Print summary
print(f"📊 Overview:")
print(f"   Total Events: {len(rows)}")
print(f"   Unique Sessions: {len(all_sessions)}")
print(f"   Projects Active: {len(all_projects)}")
print(f"")

print(f"📁 Projects Worked On:")
for proj in sorted(all_projects):
    if proj:
        print(f"   - {proj}")
print(f"")

print(f"📅 Activity by Date:")
for date in sorted(by_date.keys(), reverse=True):
    projects = by_date[date]
    total_events = sum(len(events) for events in projects.values())
    print(f"")
    print(f"   {date}: {total_events} events across {len(projects)} project(s)")
    for proj, events in projects.items():
        ops = defaultdict(int)
        for e in events:
            ops[e['operation'] or 'unknown'] += 1
        op_summary = ', '.join(f"{k}: {v}" for k, v in sorted(ops.items(), key=lambda x: -x[1])[:3])
        print(f"      └─ {proj}: {op_summary}")

print(f"")
print(f"💬 Recent User Prompts (Last 5):")
for p in user_prompts[:5]:
    print(f"   [{p['date']}] {p['prompt']}...")

print(f"")
print(f"=" * 60)
print(f"  Context loaded. Ready to continue work.")
print(f"=" * 60)
print(f"")

cursor.close()
conn.close()
PYTHON_EOF
```

## Examples

**Load last 3 days (default):**
```
/load-context
```

**Load last week:**
```
/load-context --days 7
```

**Load last 2 weeks:**
```
/load-context --days 14
```

## When to Use

- **Returning after vacation** - Load 7-14 days
- **Monday morning** - Load 3-5 days to see Friday's work
- **Picking up someone else's work** - Load relevant time period

## Comparison with Other Commands

| Command | Time Window | Use Case |
|---------|-------------|----------|
| `/prime-agent` | 2 hours | Same-day context, session continuation |
| `/load-context` | 1-14 days | Multi-day context, returning after absence |
| `/activity-query` | Configurable | Raw query results, investigation |

## Related Commands

- `/prime-agent` - Full context priming with 2-hour activity review
- `/activity-query` - Query specific activity
- `/export-context` - Export activity to file
