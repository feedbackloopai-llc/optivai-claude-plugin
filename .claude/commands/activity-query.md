# Activity Query - Query Activity from PostgreSQL

**Purpose**: Query your Claude Code activity directly from PostgreSQL's "The Well"
**Usage**: `/activity-query [hours]`

Default: Last 24 hours. Pass a number to change the time window.

## Query Recent Activity

```bash
# Query activity from The Well (last 24 hours by default)
python3 <<'PYTHON_EOF'
import sys
import json
from pathlib import Path
from datetime import datetime

# Parse hours argument (default 24)
hours = 24
if len(sys.argv) > 1:
    try:
        hours = int(sys.argv[1])
    except ValueError:
        pass

# Load config for connection details
config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
if not config_file.exists():
    print(f"Error: Config file not found: {config_file}")
    print("PostgreSQL connection not configured.")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = json.load(f)

sf_config = config.get('destinations', {}).get('postgresql', {})
if not sf_config.get('enabled', False):
    print("PostgreSQL sync is disabled in configuration.")
    sys.exit(1)

try:
    import psycopg2.connector
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Error: Required packages not installed.")
    print("Run: pip install psycopg2-binary cryptography")
    sys.exit(1)

# Load private key
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

# Connect to PostgreSQL
conn = psycopg2.connect(
    account=sf_config['account'],
    user=sf_config['auth']['user'],
    private_key=pkb,
    warehouse=sf_config['warehouse'],
    role=sf_config.get('role', 'ACCOUNTADMIN')
)

table = sf_config.get('target_table', 'DW_DEV_STREAM.LANDING.RAW_EVENTS')
tenant_id = sf_config.get('tenant_id', 'CLAUDE_CODE')

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
  AND EVENT_AT > DATEADD(hour, -{hours}, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC
LIMIT 50
"""

print(f"=== Claude Code Activity (Last {hours} Hours) ===")
print(f"Source: {table}")
print("")

cursor = conn.cursor()
cursor.execute(query)
rows = cursor.fetchall()

if not rows:
    print("No activity found in the specified time window.")
else:
    print(f"Found {len(rows)} events:\n")
    for row in rows:
        event_at, event_type, project, operation, prompt, session_id = row
        prompt_preview = (prompt[:60] + '...') if prompt and len(prompt) > 60 else (prompt or '')
        print(f"[{event_at}] {operation or event_type}")
        print(f"  Project: {project}")
        if prompt_preview:
            print(f"  Prompt: {prompt_preview}")
        print("")

cursor.close()
conn.close()

print("=== End Activity Query ===")
PYTHON_EOF
```

## Query Options

**Last 2 hours:**
```bash
/activity-query 2
```

**Last 48 hours:**
```bash
/activity-query 48
```

## Custom SQL Query

For more complex queries, connect directly to PostgreSQL:

```sql
-- Activity by operation type
SELECT
    METADATA:operation::STRING as operation,
    COUNT(*) as count
FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -7, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 2 DESC;

-- Activity by project
SELECT
    SUBJECT_ID as project,
    COUNT(*) as event_count,
    MIN(EVENT_AT) as first_event,
    MAX(EVENT_AT) as last_event
FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -7, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 2 DESC;
```

## Related Commands

- `/sync-status` - Check sync status
- `/sync-now` - Manually trigger sync
- `/history` - View local activity history
