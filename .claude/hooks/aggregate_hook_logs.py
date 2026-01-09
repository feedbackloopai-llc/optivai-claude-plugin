#!/usr/bin/env python3
"""
Hook Log Aggregation Script for Claude Code Memory System

Purpose: Rebuild work_log.yaml from hook logs after corruption.

Usage:
    # Show statistics about available logs
    python aggregate_hook_logs.py --stats

    # Recover all available logs (with backup)
    python aggregate_hook_logs.py --recover

    # Preview what would be imported
    python aggregate_hook_logs.py --recover --dry-run

    # Recover specific date range
    python aggregate_hook_logs.py --from-date 2026-01-05 --to-date 2026-01-06

Key Features:
    - Searches all known locations for hook logs
    - Hash-based deduplication (won't re-import existing entries)
    - Creates backup before modifying work_log.yaml
    - Transforms hook log format to work_log format
    - Filters to significant operations only
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Set

# Optional YAML support
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    print("Warning: PyYAML not installed. Output will be in JSON format.")

# Memory system paths
MEMORY_DIR = Path.home() / ".claude" / "gz-observability-memory"
WORK_LOG_FILE = MEMORY_DIR / "work_log.yaml"
BACKUP_DIR = MEMORY_DIR / "backups"

# Significant operations to include
SIGNIFICANT_OPERATIONS = {
    'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write', 'ask_user'
}


def get_hook_log_locations() -> List[Path]:
    """Get all known hook log locations to search."""
    locations = []

    # Standard global location
    locations.append(Path.home() / ".claude" / "logs")

    # Search common project directories for .claude/logs
    common_dirs = [
        Path.home() / "Documents",
        Path.home() / "Projects",
        Path.home() / "repos",
        Path.home() / "code",
        Path.home() / "dev",
        Path.home() / "workspace",
    ]

    for base_dir in common_dirs:
        if base_dir.exists():
            try:
                # Find .claude/logs directories (limit depth to avoid long searches)
                for item in base_dir.iterdir():
                    if item.is_dir():
                        claude_logs = item / ".claude" / "logs"
                        if claude_logs.exists():
                            locations.append(claude_logs)
                        # Check one more level
                        for subitem in item.iterdir():
                            if subitem.is_dir():
                                claude_logs = subitem / ".claude" / "logs"
                                if claude_logs.exists():
                                    locations.append(claude_logs)
            except PermissionError:
                continue

    return list(set(locations))


def find_log_files(locations: List[Path],
                   from_date: str = None,
                   to_date: str = None) -> List[Path]:
    """Find all log files within date range."""
    log_files = []

    for log_dir in locations:
        if not log_dir.exists():
            continue

        for log_file in log_dir.glob("agent-activity-*.log"):
            file_date = log_file.stem.replace("agent-activity-", "")

            if from_date and file_date < from_date:
                continue
            if to_date and file_date > to_date:
                continue

            log_files.append(log_file)

    return sorted(log_files)


def compute_entry_hash(entry: Dict) -> str:
    """Compute hash for deduplication."""
    key_fields = [
        entry.get('timestamp', ''),
        entry.get('operation', ''),
        entry.get('prompt', '')[:100],
        entry.get('session_id', ''),
    ]
    key_string = '|'.join(str(f) for f in key_fields)
    return hashlib.md5(key_string.encode()).hexdigest()[:12]


def transform_hook_entry_to_work_log(hook_entry: Dict) -> Dict:
    """Transform hook log entry to work_log format."""
    return {
        'timestamp': hook_entry.get('timestamp', ''),
        'local_time': hook_entry.get('time', ''),
        'operation': hook_entry.get('operation', ''),
        'description': hook_entry.get('prompt', '')[:200],
        'project': hook_entry.get('project', ''),
        'cwd': hook_entry.get('cwd', ''),
        'details': hook_entry.get('details', {}),
        '_imported_from': 'hook_logs',
        '_import_time': datetime.now(timezone.utc).isoformat()
    }


def load_existing_work_log() -> Dict[str, Any]:
    """Load existing work log."""
    if not WORK_LOG_FILE.exists():
        return {'entries': []}

    try:
        with open(WORK_LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                return {'entries': []}

            if YAML_AVAILABLE:
                data = yaml.safe_load(content) or {}
            else:
                data = json.loads(content) or {}

            if 'entries' not in data:
                data['entries'] = []

            return data
    except Exception as e:
        print(f"Error loading work log: {e}")
        return {'entries': []}


def get_existing_hashes(work_log: Dict) -> Set[str]:
    """Get hashes of existing entries for deduplication."""
    hashes = set()
    for entry in work_log.get('entries', []):
        h = compute_entry_hash(entry)
        hashes.add(h)
    return hashes


def create_backup() -> Path:
    """Create backup of current work_log.yaml."""
    if not WORK_LOG_FILE.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f"work_log_pre_recovery_{timestamp}.yaml"
    backup_path = BACKUP_DIR / backup_name

    shutil.copy2(WORK_LOG_FILE, backup_path)
    return backup_path


def show_stats(locations: List[Path], from_date: str, to_date: str):
    """Show statistics about available logs."""
    print("\n" + "=" * 60)
    print("HOOK LOG STATISTICS")
    print("=" * 60)

    print(f"\nSearching {len(locations)} locations:")
    for loc in locations:
        exists = "OK" if loc.exists() else "NOT FOUND"
        print(f"  - {loc} [{exists}]")

    log_files = find_log_files(locations, from_date, to_date)
    print(f"\nFound {len(log_files)} log files in date range")

    if not log_files:
        print("\nNo log files found.")
        return

    total_entries = 0
    significant_entries = 0
    entries_by_project = {}
    entries_by_operation = {}
    date_range = set()

    for log_file in log_files:
        file_date = log_file.stem.replace("agent-activity-", "")
        date_range.add(file_date)

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        total_entries += 1

                        op = entry.get('operation', 'unknown')
                        entries_by_operation[op] = entries_by_operation.get(op, 0) + 1

                        if op in SIGNIFICANT_OPERATIONS:
                            significant_entries += 1
                            project = entry.get('project', 'unknown')
                            entries_by_project[project] = entries_by_project.get(project, 0) + 1

                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading {log_file}: {e}")

    print(f"\nDate range: {min(date_range)} to {max(date_range)}")
    print(f"Total entries: {total_entries}")
    print(f"Significant entries (recoverable): {significant_entries}")

    print("\nEntries by operation:")
    for op, count in sorted(entries_by_operation.items(), key=lambda x: x[1], reverse=True):
        sig = "*" if op in SIGNIFICANT_OPERATIONS else " "
        print(f"  {sig} {op}: {count}")

    print("\nSignificant entries by project:")
    for project, count in sorted(entries_by_project.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {project}: {count}")

    # Check existing work log
    work_log = load_existing_work_log()
    existing_count = len(work_log.get('entries', []))
    existing_hashes = get_existing_hashes(work_log)

    print(f"\nExisting work_log entries: {existing_count}")

    # Count potential new entries
    potential_new = 0
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get('operation') in SIGNIFICANT_OPERATIONS:
                            h = compute_entry_hash(entry)
                            if h not in existing_hashes:
                                potential_new += 1
                    except:
                        continue
        except:
            continue

    print(f"Potential new entries to recover: {potential_new}")
    print("\n" + "=" * 60)


def recover(locations: List[Path],
            from_date: str,
            to_date: str,
            dry_run: bool = False):
    """Recover entries from hook logs into work_log."""
    print("\n" + "=" * 60)
    print("HOOK LOG RECOVERY")
    print("=" * 60)

    if dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")

    log_files = find_log_files(locations, from_date, to_date)
    print(f"Found {len(log_files)} log files to process")

    if not log_files:
        print("No log files found. Nothing to recover.")
        return

    # Load existing work log
    work_log = load_existing_work_log()
    existing_hashes = get_existing_hashes(work_log)
    original_count = len(work_log.get('entries', []))

    print(f"Existing work_log entries: {original_count}")
    print(f"Existing entry hashes: {len(existing_hashes)}")

    # Collect new entries
    new_entries = []
    duplicates_skipped = 0

    for log_file in log_files:
        file_date = log_file.stem.replace("agent-activity-", "")
        print(f"Processing {log_file.name}...", end=" ")

        file_new = 0
        file_skipped = 0

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)

                        if entry.get('operation') not in SIGNIFICANT_OPERATIONS:
                            continue

                        entry_hash = compute_entry_hash(entry)
                        if entry_hash in existing_hashes:
                            duplicates_skipped += 1
                            file_skipped += 1
                            continue

                        # Transform and add
                        work_entry = transform_hook_entry_to_work_log(entry)
                        new_entries.append(work_entry)
                        existing_hashes.add(entry_hash)
                        file_new += 1

                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            print(f"Error: {e}")
            continue

        print(f"{file_new} new, {file_skipped} duplicates")

    print(f"\nTotal new entries to import: {len(new_entries)}")
    print(f"Duplicates skipped: {duplicates_skipped}")

    if not new_entries:
        print("No new entries to recover.")
        return

    if dry_run:
        print("\n*** DRY RUN - Would have imported entries. Run without --dry-run to execute. ***")
        return

    # Create backup before modifying
    backup_path = create_backup()
    if backup_path:
        print(f"\nBackup created: {backup_path}")

    # Merge entries
    all_entries = work_log.get('entries', []) + new_entries

    # Sort by timestamp
    all_entries.sort(key=lambda x: x.get('timestamp', ''))

    # Update work log
    work_log['entries'] = all_entries
    work_log['last_updated'] = datetime.now(timezone.utc).isoformat()
    work_log['entry_count'] = len(all_entries)
    work_log['last_recovery'] = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'entries_recovered': len(new_entries),
        'source_files': len(log_files)
    }

    # Ensure directory exists
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # Save
    try:
        with open(WORK_LOG_FILE, 'w', encoding='utf-8') as f:
            if YAML_AVAILABLE:
                yaml.dump(work_log, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            else:
                json.dump(work_log, f, indent=2)

        print(f"\nRecovery complete!")
        print(f"  Previous entries: {original_count}")
        print(f"  New entries: {len(new_entries)}")
        print(f"  Total entries: {len(all_entries)}")
        print(f"  Work log saved to: {WORK_LOG_FILE}")

    except Exception as e:
        print(f"\nError saving work log: {e}")
        if backup_path:
            print(f"Backup available at: {backup_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Aggregate hook logs into work_log.yaml for recovery',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --stats                              # Show statistics
  %(prog)s --recover                            # Recover all available logs
  %(prog)s --recover --dry-run                  # Preview what would be imported
  %(prog)s --from-date 2026-01-05 --recover     # Recover from specific date
  %(prog)s --from-date 2026-01-05 --to-date 2026-01-06 --recover
        """
    )

    parser.add_argument('--stats', action='store_true',
                        help='Show statistics about available logs')
    parser.add_argument('--recover', action='store_true',
                        help='Recover entries into work_log.yaml')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview recovery without making changes')
    parser.add_argument('--from-date', type=str,
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to-date', type=str,
                        help='End date (YYYY-MM-DD)')
    parser.add_argument('--days-back', type=int, default=90,
                        help='Days to look back (default: 90)')

    args = parser.parse_args()

    # Calculate date range
    if not args.from_date:
        from_date = (datetime.now() - timedelta(days=args.days_back)).strftime('%Y-%m-%d')
    else:
        from_date = args.from_date

    to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

    # Find log locations
    locations = get_hook_log_locations()

    if args.stats:
        show_stats(locations, from_date, to_date)
    elif args.recover:
        recover(locations, from_date, to_date, dry_run=args.dry_run)
    else:
        # Default: show stats
        show_stats(locations, from_date, to_date)
        print("\nUse --recover to import entries into work_log.yaml")
        print("Use --recover --dry-run to preview without changes")


if __name__ == "__main__":
    main()
