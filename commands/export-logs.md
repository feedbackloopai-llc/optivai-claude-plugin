---
name: export-logs
description: Export activity logs for analysis or archival
---

# Export Activity Logs

Export Claude Code activity logs to various formats for analysis, sharing, or archival.

## Arguments

- `$ARGUMENTS` - Optional: output format (json, csv, markdown) and optional date range

## Usage Examples

```bash
# Export today's logs as JSON
/export-logs

# Export as CSV
/export-logs csv

# Export as Markdown report
/export-logs markdown

# Export specific date
/export-logs json 2025-12-09

# Export date range (if implemented)
/export-logs csv 2025-12-01 2025-12-09
```

## Export Logs

```bash
python3 << 'PYTHON_EOF'
import json
import csv
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter

# Parse arguments
args = "$ARGUMENTS".strip().split()
export_format = args[0].lower() if args and args[0] else "json"
export_date = args[1] if len(args) > 1 else datetime.now().strftime("%Y-%m-%d")

log_dir = Path(".claude/logs")
if not log_dir.exists():
    print("❌ No logs directory found. Logging may not be enabled.")
    print("Run: /enable-logging")
    sys.exit(1)

# Find log file
log_file = log_dir / f"agent-activity-{export_date}.log"
if not log_file.exists():
    print(f"❌ No log file found for {export_date}: {log_file}")
    print("\nAvailable log files:")
    for f in sorted(log_dir.glob("agent-activity-*.log")):
        print(f"  - {f.name}")
    sys.exit(1)

# Read all entries
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
    print(f"📝 No entries found in {log_file.name}")
    sys.exit(0)

# Generate output filename
output_base = f"activity-log-{export_date}"

# Export based on format
if export_format == "json":
    # JSON export (pretty-printed)
    output_file = f"{output_base}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "export_date": export_date,
            "export_time": datetime.now().isoformat(),
            "total_entries": len(entries),
            "entries": entries
        }, f, indent=2)
    print(f"✅ Exported {len(entries)} entries to: {output_file}")

elif export_format == "csv":
    # CSV export
    output_file = f"{output_base}.csv"
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        if entries:
            # Get all unique keys
            all_keys = set()
            for entry in entries:
                all_keys.update(entry.keys())

            writer = csv.DictWriter(f, fieldnames=sorted(all_keys))
            writer.writeheader()
            writer.writerows(entries)
    print(f"✅ Exported {len(entries)} entries to: {output_file}")

elif export_format == "markdown":
    # Markdown report export
    output_file = f"{output_base}.md"

    # Analyze data
    operations = Counter(e.get('operation', 'unknown') for e in entries)
    sessions = set(e.get('session_id', '') for e in entries)
    hourly = Counter()
    for e in entries:
        time_str = e.get('time', '00:00:00')
        hour = time_str.split(':')[0] if ':' in time_str else '00'
        hourly[hour] += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# Claude Code Activity Log\n\n")
        f.write(f"**Date:** {export_date}\n")
        f.write(f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Total Entries:** {len(entries)}\n")
        f.write(f"**Active Sessions:** {len(sessions)}\n\n")

        f.write("## Summary Statistics\n\n")
        f.write("### Operations by Type\n\n")
        for op, count in operations.most_common():
            f.write(f"- **{op}**: {count}\n")

        f.write("\n### Activity by Hour\n\n")
        for hour in sorted(hourly.keys()):
            bar = '█' * (hourly[hour] // 5 + 1)
            f.write(f"- **{hour}:00**: {hourly[hour]} {bar}\n")

        f.write("\n## Detailed Log Entries\n\n")
        for i, entry in enumerate(entries, 1):
            time = entry.get('time', 'N/A')
            operation = entry.get('operation', 'unknown')
            prompt = entry.get('prompt', '')
            session_id = entry.get('session_id', 'N/A')[:12]

            f.write(f"### Entry {i} - [{time}] {operation.upper()}\n\n")
            f.write(f"**Operation:** `{operation}`\n")
            f.write(f"**Session:** `{session_id}...`\n")
            f.write(f"**Prompt:** {prompt}\n\n")

            if 'result' in entry:
                f.write(f"**Result:** {entry['result']}\n\n")

            f.write("---\n\n")

    print(f"✅ Exported markdown report to: {output_file}")

else:
    print(f"❌ Unknown format: {export_format}")
    print("Available formats: json, csv, markdown")
    sys.exit(1)

# Show summary
print(f"\n📊 Export Summary:")
print(f"  Date: {export_date}")
print(f"  Entries: {len(entries)}")
print(f"  Sessions: {len(sessions)}")
print(f"  Format: {export_format}")
print(f"  File: {output_file}")

# Show top operations
print(f"\n🔝 Top Operations:")
operations = Counter(e.get('operation', 'unknown') for e in entries)
for op, count in operations.most_common(5):
    print(f"  {op}: {count}")

PYTHON_EOF
```

## Export Formats

### JSON
- **Structure:** Nested JSON with metadata
- **Use for:** Programmatic analysis, archival, data pipelines
- **File:** `activity-log-YYYY-MM-DD.json`

### CSV
- **Structure:** Flat table format
- **Use for:** Spreadsheet analysis, Excel, data science
- **File:** `activity-log-YYYY-MM-DD.csv`

### Markdown
- **Structure:** Human-readable report with statistics
- **Use for:** Documentation, sharing, reports
- **File:** `activity-log-YYYY-MM-DD.md`

## Analyze Exported Data

### JSON Analysis
```bash
# Count operations by type
jq '.entries | group_by(.operation) | map({operation: .[0].operation, count: length})' activity-log-2025-12-09.json

# Filter by operation type
jq '.entries[] | select(.operation == "bash")' activity-log-2025-12-09.json
```

### CSV Analysis
```bash
# Import to SQLite
sqlite3 logs.db <<SQL
.mode csv
.import activity-log-2025-12-09.csv activities
SELECT operation, COUNT(*) FROM activities GROUP BY operation;
SQL
```

## Bulk Export

Export multiple days:

```bash
# Export all available logs
for log in .claude/logs/agent-activity-*.log; do
  date=$(basename $log .log | cut -d'-' -f3-)
  echo "Exporting $date..."
  /export-logs json $date
done
```

## Archive Logs

Create compressed archive:

```bash
# Archive all logs
tar -czf claude-logs-archive-$(date +%Y%m%d).tar.gz .claude/logs/

# Archive exported files
tar -czf claude-exports-$(date +%Y%m%d).tar.gz activity-log-*.{json,csv,md}
```

## Share Logs

Export logs for sharing (anonymize if needed):

```bash
# Export and review before sharing
/export-logs markdown

# Review the exported file
cat activity-log-$(date +%Y-%m-%d).md

# Share anonymized version (remove sensitive data manually)
```

## Notes

- Exports preserve all original data
- Markdown reports include visualizations
- CSV exports flatten nested structures
- JSON exports maintain full structure
- Exported files created in current directory
