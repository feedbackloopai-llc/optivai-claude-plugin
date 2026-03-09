# DB Connect - Test Database Connections

**Purpose**: Test connections to PostgreSQL (Neon) before starting work
**Usage**: `/db-connect`

## Credentials Location

- **PostgreSQL**: `DATABASE_URL` env var, fallback to `auto-logger-config.json` destinations.postgresql.connection_string

**IMPORTANT**: Credentials are loaded from env vars first. Never hardcode passwords.

## Test Connection

```bash
echo "=== Database Connection Test ==="
echo ""

# Test PostgreSQL (Neon) Connection
echo "1. Testing PostgreSQL (Neon) Connection..."
python3 <<'PYTHON_EOF'
import sys, os, json
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("   ✗ PostgreSQL: psycopg2 not installed")
    print("     Run: pip install psycopg2-binary")
    sys.exit(1)

# Load credentials: prefer DATABASE_URL env var, fallback to config
database_url = os.environ.get('DATABASE_URL')
cred_source = "DATABASE_URL env var"

if not database_url:
    config_file = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    if config_file.exists():
        with open(config_file, 'r') as f:
            config = json.load(f)
        pg_config = config.get('destinations', {}).get('postgresql', {})
        if pg_config.get('enabled'):
            database_url = pg_config.get('connection_string')
            cred_source = "auto-logger-config.json"

if not database_url:
    print("   ✗ PostgreSQL: No DATABASE_URL or config found")
    sys.exit(1)

try:
    conn = psycopg2.connect(database_url)
    cursor = conn.cursor()
    cursor.execute("SELECT current_user, current_database(), version()")
    user, db, version = cursor.fetchone()

    # Check pgvector
    cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    pgvector = cursor.fetchone()

    # Check brain schema
    cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = 'brain'")
    has_brain = cursor.fetchone()[0] > 0

    cursor.close()
    conn.close()

    print(f"   ✓ PostgreSQL: Connected")
    print(f"     User: {user}")
    print(f"     Database: {db}")
    print(f"     Version: {version[:40]}...")
    print(f"     pgvector: {pgvector[0] if pgvector else 'NOT INSTALLED'}")
    print(f"     Brain schema: {'✓' if has_brain else '✗ (run open_brain.py --init)'}")
    print(f"     Credentials: {cred_source}")

except Exception as e:
    print(f"   ✗ PostgreSQL: Connection failed")
    print(f"     Error: {e}")
PYTHON_EOF

echo ""

# Test local log directory
echo "2. Checking Local Log Directory..."
LOG_DIR="$HOME/.claude/logs"
if [ -d "$LOG_DIR" ]; then
    file_count=$(ls -1 "$LOG_DIR"/*.log 2>/dev/null | wc -l | tr -d ' ')
    echo "   ✓ Log Directory: $LOG_DIR"
    echo "     Log files: $file_count"
else
    echo "   ⚠ Log Directory: Not found"
    echo "     Expected: $LOG_DIR"
fi

echo ""
echo "=== Connection Test Complete ==="
```

## Credential Sources

**Primary (env var):**
- `DATABASE_URL` - PostgreSQL connection string (e.g., `postgresql://user:pass@host/db?sslmode=require`)

**Fallback (config file):**
- `~/.claude/hooks/auto-logger-config.json` → `destinations.postgresql.connection_string`

## Related Commands

- `/sync-status` - Check PostgreSQL sync status
- `/activity-query` - Query activity from PostgreSQL
