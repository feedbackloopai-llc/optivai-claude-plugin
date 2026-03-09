# Export Context - Export Activity from PostgreSQL

**Purpose**: Export Claude Code activity from PostgreSQL to a local file for analysis or sharing
**Usage**: `/export-context [--hours N] [--days N] [--format json|csv|md] [--output filename]`

## Export Activity Data

```bash
python3 <<'PYTHON_EOF'
import sys
import json
import csv
from pathlib import Path
from datetime import datetime
import argparse

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument('--hours', type=int, default=0)
parser.add_argument('--days', type=int, default=1)
parser.add_argument('--format', choices=['json', 'csv', 'md'], default='json')
parser.add_argument('--output', type=str, default='')
args, _ = parser.parse_known_args()

# Calculate time window
if args.hours > 0:
    hours = args.hours
else:
    hours = args.days * 24

# Load PostgreSQL config
config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
if not config_file.exists():
    print("Error: PostgreSQL configuration not found")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = json.load(f)

sf_config = config.get('destinations', {}).get('postgresql', {})
if not sf_config.get('enabled'):
    print("Error: PostgreSQL sync not enabled")
    sys.exit(1)

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

query = f"""
SELECT
    EVENT_ID,
    EVENT_AT,
    EVENT_TYPE,
    SUBJECT_ID as PROJECT,
    ACTOR_ID,
    METADATA
FROM {table}
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND TENANT_ID = '{tenant_id}'
  AND EVENT_AT > DATEADD(hour, -{hours}, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC
"""

cursor = conn.cursor()
cursor.execute(query)
rows = cursor.fetchall()
columns = ['event_id', 'event_at', 'event_type', 'project', 'actor_id', 'metadata']

# Generate output filename
if args.output:
    output_file = Path(args.output)
else:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = Path(f'claude_activity_export_{timestamp}.{args.format}')

# Export based on format
if args.format == 'json':
    data = []
    for row in rows:
        entry = dict(zip(columns, row))
        entry['event_at'] = entry['event_at'].isoformat() if entry['event_at'] else None
        if isinstance(entry['metadata'], str):
            try:
                entry['metadata'] = json.loads(entry['metadata'])
            except:
                pass
        data.append(entry)

    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2, default=str)

elif args.format == 'csv':
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([str(v) if v else '' for v in row])

elif args.format == 'md':
    with open(output_file, 'w') as f:
        f.write(f"# Claude Code Activity Export\n\n")
        f.write(f"**Exported:** {datetime.now().isoformat()}\n")
        f.write(f"**Time Window:** Last {hours} hours\n")
        f.write(f"**Events:** {len(rows)}\n\n")
        f.write("---\n\n")

        for row in rows:
            entry = dict(zip(columns, row))
            metadata = entry['metadata']
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}

            f.write(f"## {entry['event_at']}\n\n")
            f.write(f"- **Event Type:** {entry['event_type']}\n")
            f.write(f"- **Project:** {entry['project']}\n")
            if metadata.get('operation'):
                f.write(f"- **Operation:** {metadata.get('operation')}\n")
            if metadata.get('prompt'):
                prompt = metadata.get('prompt', '')[:200]
                f.write(f"- **Prompt:** {prompt}{'...' if len(metadata.get('prompt', '')) > 200 else ''}\n")
            f.write("\n")

cursor.close()
conn.close()

print(f"✓ Exported {len(rows)} events to {output_file}")
print(f"  Format: {args.format.upper()}")
print(f"  Time window: Last {hours} hours")
PYTHON_EOF
```

## Examples

**Export last 24 hours as JSON (default):**
```
/export-context
```

**Export last 8 hours as CSV:**
```
/export-context --hours 8 --format csv
```

**Export last 7 days as Markdown:**
```
/export-context --days 7 --format md --output weekly_activity.md
```

## Output Formats

| Format | Description | Best For |
|--------|-------------|----------|
| `json` | Full structured data | Programmatic analysis, import to other tools |
| `csv` | Tabular data | Excel, spreadsheet analysis |
| `md` | Human-readable markdown | Documentation, sharing, reports |

## Related Commands

- `/activity-query` - Query activity interactively
- `/summary` - Get session summary
- `/sync-status` - Check sync status
