# Summary - Session Activity Summary from PostgreSQL

**Purpose**: Generate a summary of the current session's activity from PostgreSQL
**Usage**: `/summary [--hours N]` (default: current session or last 4 hours)

## Generate Session Summary

```bash
python3 <<'PYTHON_EOF'
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# Parse arguments
hours = 4
for i, arg in enumerate(sys.argv[1:]):
    if arg == '--hours' and i + 2 < len(sys.argv):
        try:
            hours = int(sys.argv[i + 2])
        except ValueError:
            pass

# Try to get current session ID
session_state_file = Path.home() / ".claude" / "logs" / ".session_state.json"
current_session = None
if session_state_file.exists():
    try:
        with open(session_state_file, 'r') as f:
            state = json.load(f)
            current_session = state.get('session_id')
    except:
        pass

# Load PostgreSQL config
config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
if not config_file.exists():
    print("Error: PostgreSQL configuration not found")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = json.load(f)

sf_config = config.get('destinations', {}).get('postgresql', {})
if not sf_config.get('enabled'):
    print("PostgreSQL sync not enabled. Enable with /config-logs")
    sys.exit(1)

try:
    import psycopg2.connector
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Error: Required packages not installed")
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

# Build query - filter by session if available, otherwise by time
if current_session:
    where_clause = f"METADATA:session_id::STRING LIKE '{current_session[:20]}%'"
    scope_desc = f"Session: {current_session[:30]}..."
else:
    where_clause = f"EVENT_AT > DATEADD(hour, -{hours}, CURRENT_TIMESTAMP())"
    scope_desc = f"Last {hours} hours"

query = f"""
SELECT
    EVENT_AT,
    EVENT_TYPE,
    SUBJECT_ID as PROJECT,
    METADATA:operation::STRING as OPERATION,
    METADATA:prompt::STRING as PROMPT,
    METADATA:session_id::STRING as SESSION_ID
FROM {table}
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND TENANT_ID = '{tenant_id}'
  AND {where_clause}
ORDER BY EVENT_AT ASC
"""

cursor = conn.cursor()
cursor.execute(query)
rows = cursor.fetchall()

print("")
print("=" * 60)
print("  SESSION ACTIVITY SUMMARY")
print("=" * 60)
print(f"  Scope: {scope_desc}")
print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("")

if not rows:
    print("  No activity found for this session/time period.")
    print("")
    print("  Tips:")
    print("  - Run /sync-now to sync recent activity to PostgreSQL")
    print("  - Use --hours N to expand the time window")
    print("")
    cursor.close()
    conn.close()
    sys.exit(0)

# Calculate metrics
first_event = rows[0][0]
last_event = rows[-1][0]
duration = last_event - first_event if first_event and last_event else timedelta(0)

# Count by operation
op_counts = defaultdict(int)
projects = set()
user_prompts = []

for row in rows:
    event_at, event_type, project, operation, prompt, session_id = row
    op_counts[operation or event_type] += 1
    if project:
        projects.add(project)
    if operation == 'user_prompt' and prompt:
        user_prompts.append(prompt[:100])

print("📊 Overview:")
print(f"   Total Events: {len(rows)}")
print(f"   Duration: {duration}")
print(f"   First Activity: {first_event}")
print(f"   Last Activity: {last_event}")
print("")

print("📁 Projects:")
for proj in sorted(projects):
    print(f"   - {proj}")
print("")

print("🔧 Operations:")
for op, count in sorted(op_counts.items(), key=lambda x: -x[1])[:10]:
    bar = "█" * min(count, 20)
    print(f"   {op:20s} {count:4d} {bar}")
print("")

print("💬 User Prompts ({} total):".format(len(user_prompts)))
for i, prompt in enumerate(user_prompts[:5]):
    print(f"   {i+1}. {prompt}...")
if len(user_prompts) > 5:
    print(f"   ... and {len(user_prompts) - 5} more")
print("")

# Key accomplishments (heuristic based on operations)
print("✅ Session Highlights:")
if op_counts.get('write', 0) > 0:
    print(f"   - Created/modified {op_counts['write']} file(s)")
if op_counts.get('bash', 0) > 0:
    print(f"   - Executed {op_counts['bash']} command(s)")
if op_counts.get('read', 0) > 0:
    print(f"   - Read {op_counts['read']} file(s)")
if op_counts.get('edit', 0) > 0:
    print(f"   - Made {op_counts['edit']} edit(s)")
if op_counts.get('glob', 0) + op_counts.get('grep', 0) > 0:
    print(f"   - Performed {op_counts.get('glob', 0) + op_counts.get('grep', 0)} search(es)")
print("")

print("=" * 60)
print("  Summary complete.")
print("=" * 60)
print("")

cursor.close()
conn.close()
PYTHON_EOF
```

## Examples

**Summarize current session:**
```
/summary
```

**Summarize last 8 hours:**
```
/summary --hours 8
```

## What's Included

- **Overview** - Event count, duration, time range
- **Projects** - Which codebases were worked on
- **Operations** - Breakdown of tool usage (read, write, bash, etc.)
- **User Prompts** - Recent requests made
- **Highlights** - Key accomplishments (files created, commands run)

## Related Commands

- `/new-session` - Start a fresh session
- `/load-context` - Load multi-day historical context
- `/export-context` - Export activity to file
- `/activity-query` - Raw activity query
