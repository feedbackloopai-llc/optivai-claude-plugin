#!/usr/bin/env python3
"""
Hook Log Aggregation Script for Claude Code Memory System

This script aggregates activity from per-project hook logs (.claude/logs/agent-activity-*.log)
into the global memory system (~/.claude/gz-observability-memory/work_log.yaml).

Use Cases:
1. Recovery: Rebuild work_log.yaml from hook logs after corruption
2. Sync: Regular aggregation of hook logs to memory system
3. Historical Import: Import older hook logs that weren't captured by memory system

The hook logs are the authoritative source of truth - this script syncs that data
to the memory system for easier querying and session recovery.

Usage:
    # Recover all available hook logs
    python aggregate_hook_logs.py --recover

    # Recover specific date range
    python aggregate_hook_logs.py --from-date 2026-01-05 --to-date 2026-01-06

    # Dry run to see what would be imported
    python aggregate_hook_logs.py --recover --dry-run

    # Show statistics about available logs
    python aggregate_hook_logs.py --stats

Location: scripts/aggregate_hook_logs.py
"""

import os
import sys
import json
import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from collections import defaultdict

# Try to import yaml
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# Configuration
MEMORY_DIR = Path.home() / ".claude" / "gz-observability-memory"
WORK_LOG_FILE = MEMORY_DIR / "work_log.yaml"
BACKUP_DIR = MEMORY_DIR / "backups"

# Common locations to search for hook logs
SEARCH_PATHS = [
    Path.home() / ".claude" / "logs",  # Global logs
    Path.home() / "Documents",          # Documents folder and subprojects
    Path.home() / "Projects",           # Common projects folder
    Path.home() / "Code",               # Common code folder
]

# Significant operations to include in work_log (matches memory_writer.py)
SIGNIFICANT_OPS = {'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write'}


def generate_entry_hash(entry: Dict[str, Any]) -> str:
    """Generate a unique hash for an entry to detect duplicates."""
    # Use timestamp + operation + description prefix for uniqueness
    key = f"{entry.get('timestamp', '')}|{entry.get('operation', '')}|{entry.get('description', '')[:50]}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def find_hook_log_files(search_paths: List[Path] = None,
                         from_date: Optional[str] = None,
                         to_date: Optional[str] = None) -> List[Path]:
    """
    Find all hook log files (agent-activity-*.log) in search paths.

    Args:
        search_paths: List of paths to search (default: SEARCH_PATHS)
        from_date: Optional start date filter (YYYY-MM-DD)
        to_date: Optional end date filter (YYYY-MM-DD)

    Returns:
        List of Path objects to log files
    """
    if search_paths is None:
        search_paths = SEARCH_PATHS

    log_files = set()

    for base_path in search_paths:
        if not base_path.exists():
            continue

        # Search recursively for .claude/logs directories
        try:
            for claude_dir in base_path.rglob(".claude/logs"):
                if claude_dir.is_dir():
                    for log_file in claude_dir.glob("agent-activity-*.log"):
                        if log_file.is_file():
                            # Apply date filter if specified
                            if from_date or to_date:
                                # Extract date from filename: agent-activity-YYYY-MM-DD.log
                                file_date = log_file.stem.replace("agent-activity-", "")
                                if from_date and file_date < from_date:
                                    continue
                                if to_date and file_date > to_date:
                                    continue
                            log_files.add(log_file)
        except PermissionError:
            continue

    # Also check direct log directories
    for base_path in search_paths:
        if base_path.is_dir() and base_path.name == "logs":
            for log_file in base_path.glob("agent-activity-*.log"):
                if log_file.is_file():
                    if from_date or to_date:
                        file_date = log_file.stem.replace("agent-activity-", "")
                        if from_date and file_date < from_date:
                            continue
                        if to_date and file_date > to_date:
                            continue
                    log_files.add(log_file)

    return sorted(log_files, key=lambda p: p.name)


def parse_hook_log_file(log_file: Path) -> List[Dict[str, Any]]:
    """
    Parse a hook log file (JSON Lines format) and return entries.

    Args:
        log_file: Path to the log file

    Returns:
        List of parsed log entries
    """
    entries = []

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Add source file info for tracking
                    entry['_source_file'] = str(log_file)
                    entry['_source_line'] = line_num
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error reading {log_file}: {e}", file=sys.stderr)

    return entries


def transform_hook_entry_to_work_log(hook_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transform a hook log entry to work_log.yaml format.

    Args:
        hook_entry: Entry from hook log (agent-activity-*.log)

    Returns:
        Entry in work_log.yaml format, or None if not significant
    """
    operation = hook_entry.get('operation', '')

    # Only include significant operations
    if operation not in SIGNIFICANT_OPS:
        return None

    # Build the work_log entry
    work_entry = {
        'timestamp': hook_entry.get('timestamp', ''),
        'local_time': hook_entry.get('time', ''),
        'operation': operation,
        'description': hook_entry.get('prompt', '')[:200],
    }

    # Add project context
    if hook_entry.get('project'):
        work_entry['project'] = hook_entry['project']
    if hook_entry.get('cwd'):
        work_entry['cwd'] = hook_entry['cwd']

    # Add relevant details
    details = hook_entry.get('details', {})
    if details:
        work_entry['details'] = {}
        for key, value in details.items():
            if key.startswith('_'):
                continue
            if isinstance(value, str) and len(value) > 100:
                work_entry['details'][key] = value[:100] + '...'
            else:
                work_entry['details'][key] = value

    return work_entry


def load_existing_work_log() -> Tuple[Dict[str, Any], Set[str]]:
    """
    Load existing work_log.yaml and extract entry hashes for deduplication.

    Returns:
        Tuple of (work_log_data, set of existing entry hashes)
    """
    existing_hashes = set()

    if not WORK_LOG_FILE.exists():
        return {'entries': [], 'entry_count': 0}, existing_hashes

    try:
        with open(WORK_LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                return {'entries': [], 'entry_count': 0}, existing_hashes

            if YAML_AVAILABLE:
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)

            data = data or {'entries': [], 'entry_count': 0}

            # Generate hashes for existing entries
            for entry in data.get('entries', []):
                existing_hashes.add(generate_entry_hash(entry))

            return data, existing_hashes
    except Exception as e:
        print(f"Warning: Could not load existing work_log.yaml: {e}", file=sys.stderr)
        return {'entries': [], 'entry_count': 0}, existing_hashes


def save_work_log(data: Dict[str, Any], backup: bool = True):
    """
    Save work_log.yaml with optional backup.

    Args:
        data: Work log data to save
        backup: Whether to create backup before saving
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # Create backup if requested and file exists
    if backup and WORK_LOG_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = BACKUP_DIR / f"work_log_pre_aggregate_{timestamp}.yaml"
        import shutil
        shutil.copy2(WORK_LOG_FILE, backup_path)
        print(f"Created backup: {backup_path}")

    # Update metadata
    data['last_updated'] = datetime.now(timezone.utc).isoformat()
    data['entry_count'] = len(data.get('entries', []))

    # Save
    with open(WORK_LOG_FILE, 'w', encoding='utf-8') as f:
        if YAML_AVAILABLE:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        else:
            json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved work_log.yaml with {data['entry_count']} entries")


def aggregate_logs(from_date: Optional[str] = None,
                   to_date: Optional[str] = None,
                   dry_run: bool = False,
                   max_entries: int = 500) -> Dict[str, Any]:
    """
    Aggregate hook logs into work_log.yaml.

    Args:
        from_date: Optional start date (YYYY-MM-DD)
        to_date: Optional end date (YYYY-MM-DD)
        dry_run: If True, don't save changes
        max_entries: Maximum entries to keep in work_log

    Returns:
        Statistics about the aggregation
    """
    stats = {
        'log_files_found': 0,
        'entries_parsed': 0,
        'significant_entries': 0,
        'new_entries_added': 0,
        'duplicate_entries_skipped': 0,
        'final_entry_count': 0,
        'projects_found': set(),
    }

    # Find hook log files
    log_files = find_hook_log_files(from_date=from_date, to_date=to_date)
    stats['log_files_found'] = len(log_files)

    if not log_files:
        print("No hook log files found.")
        return stats

    print(f"Found {len(log_files)} log files to process")

    # Load existing work_log and get hashes for deduplication
    work_log_data, existing_hashes = load_existing_work_log()
    existing_entries = work_log_data.get('entries', [])

    # Process each log file
    new_entries = []

    for log_file in log_files:
        entries = parse_hook_log_file(log_file)
        stats['entries_parsed'] += len(entries)

        for hook_entry in entries:
            work_entry = transform_hook_entry_to_work_log(hook_entry)

            if work_entry is None:
                continue

            stats['significant_entries'] += 1

            if work_entry.get('project'):
                stats['projects_found'].add(work_entry['project'])

            # Check for duplicates
            entry_hash = generate_entry_hash(work_entry)
            if entry_hash in existing_hashes:
                stats['duplicate_entries_skipped'] += 1
                continue

            existing_hashes.add(entry_hash)
            new_entries.append(work_entry)
            stats['new_entries_added'] += 1

    # Merge and sort entries by timestamp
    all_entries = existing_entries + new_entries
    all_entries.sort(key=lambda e: e.get('timestamp', ''))

    # Keep only last max_entries
    if len(all_entries) > max_entries:
        all_entries = all_entries[-max_entries:]

    stats['final_entry_count'] = len(all_entries)
    stats['projects_found'] = list(stats['projects_found'])

    # Save if not dry run
    if not dry_run:
        work_log_data['entries'] = all_entries
        save_work_log(work_log_data)
    else:
        print("\n[DRY RUN] Would save work_log.yaml with these changes")

    return stats


def show_stats():
    """Display statistics about available hook logs and memory state."""
    print("=" * 60)
    print("HOOK LOG STATISTICS")
    print("=" * 60)

    # Find all log files
    log_files = find_hook_log_files()

    if not log_files:
        print("\nNo hook log files found in search paths.")
        return

    # Group by project/location
    by_location = defaultdict(list)
    total_entries = 0
    date_range = {'min': None, 'max': None}

    for log_file in log_files:
        # Extract project from path
        parts = log_file.parts
        if '.claude' in parts:
            claude_idx = parts.index('.claude')
            if claude_idx > 0:
                project = parts[claude_idx - 1]
            else:
                project = 'global'
        else:
            project = 'unknown'

        by_location[project].append(log_file)

        # Count entries
        entry_count = sum(1 for _ in open(log_file, 'r'))
        total_entries += entry_count

        # Extract date from filename
        file_date = log_file.stem.replace("agent-activity-", "")
        if date_range['min'] is None or file_date < date_range['min']:
            date_range['min'] = file_date
        if date_range['max'] is None or file_date > date_range['max']:
            date_range['max'] = file_date

    print(f"\nTotal log files: {len(log_files)}")
    print(f"Total entries: ~{total_entries}")
    print(f"Date range: {date_range['min']} to {date_range['max']}")

    print("\n--- By Project/Location ---")
    for project, files in sorted(by_location.items()):
        print(f"\n{project}:")
        for f in sorted(files):
            size = f.stat().st_size
            print(f"  {f.name} ({size:,} bytes)")

    # Show memory state
    print("\n" + "=" * 60)
    print("MEMORY SYSTEM STATE")
    print("=" * 60)

    if WORK_LOG_FILE.exists():
        work_log, _ = load_existing_work_log()
        entries = work_log.get('entries', [])
        print(f"\nwork_log.yaml: {len(entries)} entries")
        if entries:
            first = entries[0].get('timestamp', 'unknown')
            last = entries[-1].get('timestamp', 'unknown')
            print(f"  Range: {first[:19]} to {last[:19]}")

            # Show projects in work_log
            projects = set(e.get('project', 'unknown') for e in entries if e.get('project'))
            if projects:
                print(f"  Projects: {', '.join(sorted(projects))}")
    else:
        print("\nwork_log.yaml: NOT FOUND")

    print()


def main():
    parser = argparse.ArgumentParser(
        description='Aggregate hook logs into memory system',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python aggregate_hook_logs.py --stats          # Show available logs
  python aggregate_hook_logs.py --recover        # Recover all available logs
  python aggregate_hook_logs.py --recover --dry-run  # Preview what would be imported
  python aggregate_hook_logs.py --from-date 2026-01-05 --to-date 2026-01-06
        """
    )

    parser.add_argument('--recover', action='store_true',
                        help='Aggregate all available hook logs into memory system')
    parser.add_argument('--from-date', type=str,
                        help='Start date filter (YYYY-MM-DD)')
    parser.add_argument('--to-date', type=str,
                        help='End date filter (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--stats', action='store_true',
                        help='Show statistics about available logs')
    parser.add_argument('--max-entries', type=int, default=500,
                        help='Maximum entries to keep in work_log (default: 500)')

    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if args.recover or args.from_date or args.to_date:
        print("=" * 60)
        print("HOOK LOG AGGREGATION")
        print("=" * 60)

        if args.from_date:
            print(f"From date: {args.from_date}")
        if args.to_date:
            print(f"To date: {args.to_date}")
        if args.dry_run:
            print("[DRY RUN MODE]")

        print()

        stats = aggregate_logs(
            from_date=args.from_date,
            to_date=args.to_date,
            dry_run=args.dry_run,
            max_entries=args.max_entries
        )

        print("\n--- Aggregation Results ---")
        print(f"Log files found: {stats['log_files_found']}")
        print(f"Total entries parsed: {stats['entries_parsed']}")
        print(f"Significant entries: {stats['significant_entries']}")
        print(f"New entries added: {stats['new_entries_added']}")
        print(f"Duplicates skipped: {stats['duplicate_entries_skipped']}")
        print(f"Final entry count: {stats['final_entry_count']}")
        if stats['projects_found']:
            print(f"Projects: {', '.join(sorted(stats['projects_found']))}")

        return

    # Default: show help
    parser.print_help()


if __name__ == "__main__":
    main()
