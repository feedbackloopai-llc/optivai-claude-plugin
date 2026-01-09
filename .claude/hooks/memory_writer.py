#!/usr/bin/env python3
"""
Enhanced Memory System for Claude Code
Implements backup/recovery, project context, and integrated context access.

Memory files (at ~/.claude/gz-observability-memory/):
- session_state.yaml: Current session context and focus
- planned_tasks.yaml: Tasks synced from TodoWrite (pending/in_progress/completed)
- work_log.yaml: Chronological history of significant operations (last 500)
- recovery_checkpoint.yaml: Crash recovery state

Additional directories:
- backups/: Timestamped backups before overwrites
- archive/: Quarterly rolloff of old entries
- corrupt/: Quarantined corrupt files for analysis

Priority 1: Prevent Future Data Loss (Backup/Recovery System)
Priority 2: Add Project Context to Work Log Entries
Priority 3: Recovery Mechanism (Hook Log Aggregation)
Priority 4: Integrated Context Access
"""

import os
import json
import shutil
import hashlib
import fnmatch
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

# Optional YAML support
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# Configuration
MEMORY_DIR = Path.home() / ".claude" / "gz-observability-memory"
BACKUP_DIR = MEMORY_DIR / "backups"
ARCHIVE_DIR = MEMORY_DIR / "archive"
CORRUPT_DIR = MEMORY_DIR / "corrupt"

# Memory file paths
SESSION_STATE_FILE = MEMORY_DIR / "session_state.yaml"
PLANNED_TASKS_FILE = MEMORY_DIR / "planned_tasks.yaml"
WORK_LOG_FILE = MEMORY_DIR / "work_log.yaml"
RECOVERY_CHECKPOINT_FILE = MEMORY_DIR / "recovery_checkpoint.yaml"

# Critical files that require backup before modification
CRITICAL_FILES = {WORK_LOG_FILE, PLANNED_TASKS_FILE}

# Configuration
MAX_BACKUPS_PER_FILE = 5
MAX_WORK_LOG_ENTRIES = 500
ARCHIVE_RETENTION_DAYS = 90


class MemoryWriter:
    """
    Enhanced memory writer with backup/recovery, project context, and integrated access.
    """

    def __init__(self):
        """Initialize the memory writer."""
        self.memory_dir = MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Create directories
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        CORRUPT_DIR.mkdir(parents=True, exist_ok=True)

        self.error_log = self.memory_dir / "memory_errors.log"

        # CRITICAL: Initialize BEFORE _load_operation_count()
        # because _load_operation_count() calls _load_yaml() which uses _corrupted_files
        self._corrupted_files = set()

        # Now safe to call
        self._operation_count = self._load_operation_count()

    # =========================================================================
    # Error Logging
    # =========================================================================

    def _log_error(self, error_msg: str):
        """Log error to memory errors log file."""
        try:
            with open(self.error_log, 'a', encoding='utf-8') as f:
                timestamp = datetime.now(timezone.utc).isoformat()
                f.write(f"{timestamp} ERROR: {error_msg}\n")
        except Exception:
            pass

    # =========================================================================
    # Backup System (Priority 1)
    # =========================================================================

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """Create timestamped backup before modification."""
        if not file_path.exists():
            return None

        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
            backup_path = BACKUP_DIR / backup_name

            shutil.copy2(file_path, backup_path)
            self._rotate_backups(file_path.stem)
            return backup_path
        except Exception as e:
            self._log_error(f"Failed to create backup for {file_path}: {e}")
            return None

    def _rotate_backups(self, file_stem: str):
        """Keep only the last N backups for a file."""
        try:
            pattern = f"{file_stem}_*.yaml"
            backups = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)

            while len(backups) > MAX_BACKUPS_PER_FILE:
                oldest = backups.pop(0)
                oldest.unlink()
        except Exception as e:
            self._log_error(f"Failed to rotate backups: {e}")

    def _try_recover_from_backup(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Attempt to recover from most recent valid backup."""
        try:
            pattern = f"{file_path.stem}_*.yaml"
            backups = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

            for backup in backups:
                try:
                    with open(backup, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if content.strip():
                            data = yaml.safe_load(content) if YAML_AVAILABLE else {}
                            if data and isinstance(data, dict):
                                self._log_error(f"Recovered {file_path.name} from backup {backup.name}")
                                return data
                except Exception:
                    continue

            return None
        except Exception as e:
            self._log_error(f"Failed to recover from backup: {e}")
            return None

    def _quarantine_corrupt_file(self, file_path: Path, error: str):
        """Move corrupt file to quarantine with metadata."""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            corrupt_name = f"{file_path.stem}_corrupt_{timestamp}{file_path.suffix}"
            corrupt_path = CORRUPT_DIR / corrupt_name
            meta_path = CORRUPT_DIR / f"{file_path.stem}_corrupt_{timestamp}.meta.txt"

            # Move corrupt file
            if file_path.exists():
                shutil.move(str(file_path), str(corrupt_path))

            # Write metadata
            with open(meta_path, 'w', encoding='utf-8') as f:
                f.write(f"Original file: {file_path}\n")
                f.write(f"Quarantine time: {datetime.now(timezone.utc).isoformat()}\n")
                f.write(f"Error: {error}\n")

            self._log_error(f"Quarantined corrupt file: {file_path.name} -> {corrupt_name}")
        except Exception as e:
            self._log_error(f"Failed to quarantine corrupt file: {e}")

    # =========================================================================
    # YAML Operations with Recovery
    # =========================================================================

    def _validate_data_structure(self, file_path: Path, data: Dict) -> bool:
        """Validate data structure based on file type."""
        if not isinstance(data, dict):
            return False

        # Specific validations
        if file_path == WORK_LOG_FILE:
            if 'entries' in data and not isinstance(data.get('entries'), list):
                return False
        elif file_path == PLANNED_TASKS_FILE:
            if 'tasks' in data and not isinstance(data.get('tasks'), list):
                return False

        return True

    def _load_yaml(self, file_path: Path) -> Dict[str, Any]:
        """Load YAML file with recovery on failure."""
        data, _ = self._load_yaml_safe(file_path)
        return data

    def _load_yaml_safe(self, file_path: Path) -> Tuple[Dict[str, Any], bool]:
        """
        Safely load YAML with backup recovery on failure.
        Returns (data, was_recovered).
        """
        if not file_path.exists():
            return ({}, False)

        # Don't re-attempt corrupted files this session
        if file_path in self._corrupted_files:
            return ({}, False)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    return ({}, False)

                if YAML_AVAILABLE:
                    data = yaml.safe_load(content) or {}
                else:
                    # Fallback: try JSON
                    try:
                        data = json.loads(content) or {}
                    except:
                        data = {"raw_content": content}

                # Validate structure
                if not self._validate_data_structure(file_path, data):
                    raise ValueError(f"Invalid data structure in {file_path.name}")

                return (data, False)

        except Exception as e:
            # For critical files, try recovery
            if file_path in CRITICAL_FILES:
                self._quarantine_corrupt_file(file_path, str(e))
                recovered = self._try_recover_from_backup(file_path)
                if recovered:
                    self._save_yaml_direct(file_path, recovered)
                    return (recovered, True)
                self._corrupted_files.add(file_path)
            else:
                self._log_error(f"Failed to load {file_path}: {e}")

            return ({}, False)

    def _save_yaml_direct(self, file_path: Path, data: Dict[str, Any]) -> bool:
        """Save YAML directly without backup (used for recovery)."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                if YAML_AVAILABLE:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                else:
                    json.dump(data, f, indent=2)
            return True
        except Exception as e:
            self._log_error(f"Failed to save {file_path}: {e}")
            return False

    def _save_yaml(self, file_path: Path, data: Dict[str, Any]) -> bool:
        """Save YAML with backup for critical files."""
        try:
            # Create backup for critical files
            if file_path in CRITICAL_FILES and file_path.exists():
                self._create_backup(file_path)

            return self._save_yaml_direct(file_path, data)
        except Exception as e:
            self._log_error(f"Failed to save {file_path}: {e}")
            return False

    # =========================================================================
    # Timestamp Utilities
    # =========================================================================

    def _get_timestamp(self) -> str:
        """Get UTC ISO timestamp."""
        return datetime.now(timezone.utc).isoformat()

    def _get_local_time(self) -> str:
        """Get local time string."""
        return datetime.now().strftime('%H:%M:%S')

    def _load_operation_count(self) -> int:
        """Load operation count from session state."""
        session = self._load_yaml(SESSION_STATE_FILE)
        return session.get('session', {}).get('operation_count', 0)

    # =========================================================================
    # Session State Management
    # =========================================================================

    def update_session_state(self, session_data: Dict[str, Any]) -> bool:
        """Update session state with new data."""
        existing = self._load_yaml(SESSION_STATE_FILE)

        existing.update(session_data)
        if 'session' not in existing:
            existing['session'] = {}
        existing['session']['last_updated'] = self._get_timestamp()
        self._operation_count += 1
        existing['session']['operation_count'] = self._operation_count

        return self._save_yaml(SESSION_STATE_FILE, existing)

    def get_session_state(self) -> Dict[str, Any]:
        """Get current session state."""
        return self._load_yaml(SESSION_STATE_FILE)

    # =========================================================================
    # Planned Tasks Management
    # =========================================================================

    def sync_planned_tasks(self, todos: List[Dict[str, Any]]) -> bool:
        """Sync tasks from TodoWrite tool."""
        data = {
            'last_synced': self._get_timestamp(),
            'tasks': todos
        }
        return self._save_yaml(PLANNED_TASKS_FILE, data)

    def get_planned_tasks(self) -> Dict[str, Any]:
        """Get planned tasks."""
        return self._load_yaml(PLANNED_TASKS_FILE)

    # =========================================================================
    # Work Log Management (Priority 2: Project Context)
    # =========================================================================

    def log_work_entry(self,
                       operation: str,
                       description: str,
                       details: Optional[Dict] = None,
                       is_significant: bool = True,
                       project: Optional[str] = None,
                       cwd: Optional[str] = None):
        """
        Add entry to work_log.yaml with project context.

        Priority 2: Added project and cwd fields to every work log entry.
        """
        if not is_significant:
            return

        entry = {
            'timestamp': self._get_timestamp(),
            'local_time': self._get_local_time(),
            'operation': operation,
            'description': description[:200] if description else '',
        }

        # Add project context (Priority 2)
        if project:
            entry['project'] = project
        if cwd:
            entry['cwd'] = cwd

        if details:
            entry['details'] = details

        # Load existing with recovery
        work_log, was_recovered = self._load_yaml_safe(WORK_LOG_FILE)
        if was_recovered:
            self._log_error("Work log was recovered from backup")

        if 'entries' not in work_log:
            work_log['entries'] = []

        work_log['entries'].append(entry)

        # Keep only last N entries
        if len(work_log['entries']) > MAX_WORK_LOG_ENTRIES:
            # Archive old entries before trimming
            self._archive_old_entries(work_log['entries'][:-MAX_WORK_LOG_ENTRIES])
            work_log['entries'] = work_log['entries'][-MAX_WORK_LOG_ENTRIES:]

        work_log['last_updated'] = self._get_timestamp()
        work_log['entry_count'] = len(work_log['entries'])

        self._save_yaml(WORK_LOG_FILE, work_log)

    def get_work_log(self) -> Dict[str, Any]:
        """Get work log."""
        return self._load_yaml(WORK_LOG_FILE)

    def _archive_old_entries(self, entries: List[Dict]):
        """Archive old entries to quarterly archive files."""
        if not entries:
            return

        try:
            # Group by quarter
            quarters = {}
            for entry in entries:
                ts = entry.get('timestamp', '')
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        q = (dt.month - 1) // 3 + 1
                        quarter_key = f"{dt.year}Q{q}"
                        quarters.setdefault(quarter_key, []).append(entry)
                    except:
                        continue

            # Write to archive files
            for quarter_key, quarter_entries in quarters.items():
                archive_file = ARCHIVE_DIR / f"work_log_{quarter_key}.yaml"
                existing = self._load_yaml(archive_file) if archive_file.exists() else {'entries': []}

                if 'entries' not in existing:
                    existing['entries'] = []

                existing['entries'].extend(quarter_entries)
                existing['last_updated'] = self._get_timestamp()

                self._save_yaml_direct(archive_file, existing)

        except Exception as e:
            self._log_error(f"Failed to archive entries: {e}")

    # =========================================================================
    # Recovery Checkpoint
    # =========================================================================

    def create_checkpoint(self, checkpoint_data: Dict[str, Any]) -> bool:
        """Create a recovery checkpoint."""
        checkpoint_data['checkpoint'] = {
            'timestamp': self._get_timestamp(),
            'status': checkpoint_data.get('status', 'in_progress')
        }
        return self._save_yaml(RECOVERY_CHECKPOINT_FILE, checkpoint_data)

    def get_recovery_checkpoint(self) -> Dict[str, Any]:
        """Get recovery checkpoint."""
        return self._load_yaml(RECOVERY_CHECKPOINT_FILE)

    # =========================================================================
    # Tool Event Handlers
    # =========================================================================

    def on_tool_use(self,
                    operation: str,
                    prompt: str,
                    details: Optional[Dict] = None,
                    project: Optional[str] = None,
                    cwd: Optional[str] = None):
        """
        Handle tool use events from hooks.

        Filters significant operations and logs to work_log.
        """
        significant_ops = {'write', 'edit', 'task', 'bash', 'todo_write', 'ask_user'}
        is_significant = operation in significant_ops

        self.log_work_entry(
            operation=operation,
            description=prompt,
            details=details,
            is_significant=is_significant,
            project=project,
            cwd=cwd
        )

    def on_user_prompt(self,
                       prompt: str,
                       details: Optional[Dict] = None,
                       project: Optional[str] = None,
                       cwd: Optional[str] = None):
        """Handle user prompt events from hooks."""
        self.log_work_entry(
            operation='user_prompt',
            description=prompt,
            details=details,
            is_significant=True,
            project=project,
            cwd=cwd
        )


# =========================================================================
# Singleton Instance
# =========================================================================

_memory_writer_instance = None


def get_memory_writer() -> MemoryWriter:
    """Get singleton memory writer instance."""
    global _memory_writer_instance
    if _memory_writer_instance is None:
        _memory_writer_instance = MemoryWriter()
    return _memory_writer_instance


# =========================================================================
# Priority 3: Hook Log Aggregation Functions
# =========================================================================

def get_hook_log_locations() -> List[Path]:
    """Get all known hook log locations to search."""
    locations = []

    # Standard locations
    locations.append(Path.home() / ".claude" / "logs")

    # Search common project directories for .claude/logs
    common_dirs = [
        Path.home() / "Documents",
        Path.home() / "Projects",
        Path.home() / "repos",
        Path.home() / "code",
        Path.home() / "dev",
    ]

    for base_dir in common_dirs:
        if base_dir.exists():
            # Find .claude/logs directories
            try:
                for claude_dir in base_dir.rglob(".claude/logs"):
                    if claude_dir.is_dir():
                        locations.append(claude_dir)
            except PermissionError:
                continue

    return list(set(locations))


def aggregate_hook_logs(days_back: int = 90,
                        from_date: str = None,
                        to_date: str = None) -> List[Dict[str, Any]]:
    """
    Aggregate hook logs from all known locations.

    Returns list of log entries sorted by timestamp.
    """
    all_entries = []

    if from_date:
        start_date = from_date
    else:
        start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    if to_date:
        end_date = to_date
    else:
        end_date = datetime.now().strftime('%Y-%m-%d')

    locations = get_hook_log_locations()

    for log_dir in locations:
        if not log_dir.exists():
            continue

        for log_file in log_dir.glob("agent-activity-*.log"):
            file_date = log_file.stem.replace("agent-activity-", "")

            if file_date < start_date or file_date > end_date:
                continue

            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            entry['_source_file'] = str(log_file)
                            entry['_file_date'] = file_date
                            all_entries.append(entry)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue

    # Sort by timestamp
    all_entries.sort(key=lambda x: x.get('timestamp', ''))

    return all_entries


# =========================================================================
# Priority 4: Integrated Context Access
# =========================================================================

def get_integrated_context(project: str = None,
                           days_back: int = 7,
                           max_entries: int = 100) -> Dict[str, Any]:
    """
    Get unified view from both memory system and hook logs.

    Combines: session_state, planned_tasks, work_log, recovery_checkpoint, hook logs
    """
    writer = get_memory_writer()

    context = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'project_filter': project,
        'days_back': days_back,
        'sources': {}
    }

    # Memory system data
    context['sources']['session_state'] = writer.get_session_state()
    context['sources']['planned_tasks'] = writer.get_planned_tasks()
    context['sources']['recovery_checkpoint'] = writer.get_recovery_checkpoint()

    # Work log (filtered by project if specified)
    work_log = writer.get_work_log()
    if project and work_log.get('entries'):
        work_log['entries'] = [
            e for e in work_log['entries']
            if e.get('project', '').lower() == project.lower()
            or project.lower() in e.get('cwd', '').lower()
        ]
    context['sources']['work_log'] = work_log

    # Hook logs
    hook_entries = aggregate_hook_logs(days_back=days_back)
    if project:
        hook_entries = [
            e for e in hook_entries
            if e.get('project', '').lower() == project.lower()
            or project.lower() in e.get('cwd', '').lower()
        ]

    # Limit entries
    context['sources']['hook_logs'] = {
        'total_entries': len(hook_entries),
        'recent_entries': hook_entries[-max_entries:] if hook_entries else []
    }

    return context


def get_project_activity_summary(project: str, days_back: int = 30) -> Dict[str, Any]:
    """
    Get activity summary for a specific project.

    Returns: operation counts, files touched, recent entries
    """
    hook_entries = aggregate_hook_logs(days_back=days_back)

    # Filter to project
    project_entries = [
        e for e in hook_entries
        if e.get('project', '').lower() == project.lower()
        or project.lower() in e.get('cwd', '').lower()
    ]

    # Count operations
    operation_counts = {}
    files_touched = set()

    for entry in project_entries:
        op = entry.get('operation', 'unknown')
        operation_counts[op] = operation_counts.get(op, 0) + 1

        details = entry.get('details', {})
        if details.get('file_path'):
            files_touched.add(details['file_path'])
        if details.get('target_file'):
            files_touched.add(details['target_file'])

    return {
        'project': project,
        'days_back': days_back,
        'total_entries': len(project_entries),
        'operation_counts': operation_counts,
        'files_touched': sorted(files_touched),
        'files_touched_count': len(files_touched),
        'recent_entries': project_entries[-20:]
    }


# =========================================================================
# Enhanced Project Context (90-day lookback, direct path search, file history)
# =========================================================================

def get_project_hook_logs(project_path: str, days_back: int = 90) -> List[Dict[str, Any]]:
    """
    Get ALL hook log entries from a specific project's .claude/logs/ directory.

    This searches the project path directly rather than relying on global search.
    """
    project_path = Path(project_path).resolve()
    project_logs_dir = project_path / ".claude" / "logs"

    if not project_logs_dir.exists():
        return []

    from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    all_entries = []

    for log_file in sorted(project_logs_dir.glob("agent-activity-*.log")):
        file_date = log_file.stem.replace("agent-activity-", "")
        if file_date < from_date:
            continue

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entry['_source_file'] = str(log_file)
                        entry['_file_date'] = file_date
                        all_entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    return all_entries


def get_file_history(project_path: str,
                     file_pattern: str = None,
                     days_back: int = 90) -> Dict[str, Any]:
    """
    Get history of file operations in a project.

    Tracks which files were modified, when, and how often.
    """
    import re

    all_entries = get_project_hook_logs(project_path, days_back)
    file_operations = {}  # file_path -> list of operations

    for entry in all_entries:
        operation = entry.get('operation', '')
        details = entry.get('details', {})

        # Extract file paths from various sources
        file_paths = set()
        if details.get('file_path'):
            file_paths.add(details['file_path'])
        if details.get('target_file'):
            file_paths.add(details['target_file'])
        if details.get('path'):
            file_paths.add(details['path'])

        # Extract from prompt for edit/write operations
        if operation in ('write', 'edit', 'read') and not file_paths:
            path_matches = re.findall(r'(/[^\s]+\.\w+)', entry.get('prompt', ''))
            for match in path_matches:
                if len(match) > 5:
                    file_paths.add(match)

        for file_path in file_paths:
            if file_pattern:
                if not fnmatch.fnmatch(Path(file_path).name, file_pattern):
                    continue

            if file_path not in file_operations:
                file_operations[file_path] = []

            file_operations[file_path].append({
                'timestamp': entry.get('timestamp', ''),
                'local_time': entry.get('time', ''),
                'operation': operation,
                'prompt': entry.get('prompt', '')[:100],
                'session_id': entry.get('session_id', '')
            })

    # Sort files by activity level
    files_by_activity = sorted(
        file_operations.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    return {
        'project_path': str(project_path),
        'days_back': days_back,
        'total_files_touched': len(file_operations),
        'total_operations': sum(len(ops) for ops in file_operations.values()),
        'most_active_files': [
            {'file': f, 'operation_count': len(ops)}
            for f, ops in files_by_activity[:15]
        ],
        'file_operations': file_operations
    }


def get_comprehensive_project_context(project_path: str,
                                       days_back: int = 90,
                                       keywords: List[str] = None,
                                       include_file_history: bool = True) -> Dict[str, Any]:
    """
    Get comprehensive context for a specific project path.

    Combines:
    - Direct project hook log search (all history in that project)
    - Memory system data filtered to project
    - File history tracking
    - Optional keyword search
    """
    project_path = Path(project_path).resolve()
    project_name = project_path.name

    context = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'project_path': str(project_path),
        'project_name': project_name,
        'days_back': days_back,
        'sources': {}
    }

    # 1. Direct project hook logs (primary source)
    hook_entries = get_project_hook_logs(str(project_path), days_back)
    significant_ops = {'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write'}
    significant_entries = [e for e in hook_entries if e.get('operation') in significant_ops]

    # Group by date
    entries_by_date = {}
    for entry in significant_entries:
        date = entry.get('_file_date', 'unknown')
        entries_by_date.setdefault(date, []).append(entry)

    context['sources']['project_hooks'] = {
        'total_entries': len(hook_entries),
        'significant_entries': len(significant_entries),
        'dates_with_activity': sorted(entries_by_date.keys()),
        'entries_by_date_count': {d: len(e) for d, e in entries_by_date.items()},
        'recent_entries': significant_entries[-20:]
    }

    # 2. Memory system data
    writer = get_memory_writer()
    context['sources']['memory_system'] = {
        'session_state': writer.get_session_state(),
        'planned_tasks': writer.get_planned_tasks(),
        'recovery_checkpoint': writer.get_recovery_checkpoint()
    }

    # Work log filtered to project
    work_log = writer.get_work_log()
    project_work_entries = [
        e for e in work_log.get('entries', [])
        if e.get('project', '').lower() == project_name.lower()
        or project_name.lower() in e.get('cwd', '').lower()
    ]
    context['sources']['memory_system']['work_log'] = {
        'total_entries': len(project_work_entries),
        'entries': project_work_entries[-30:]
    }

    # 3. File history
    if include_file_history:
        file_history = get_file_history(str(project_path), days_back=days_back)
        context['sources']['file_history'] = {
            'total_files_touched': file_history['total_files_touched'],
            'total_operations': file_history['total_operations'],
            'most_active_files': file_history['most_active_files']
        }

    # 4. Keyword search (if provided)
    if keywords:
        keyword_matches = []
        for entry in significant_entries:
            prompt = entry.get('prompt', '').lower()
            desc = entry.get('description', '').lower()
            for kw in keywords:
                if kw.lower() in prompt or kw.lower() in desc:
                    keyword_matches.append(entry)
                    break
        context['sources']['keyword_matches'] = {
            'keywords': keywords,
            'match_count': len(keyword_matches),
            'matches': keyword_matches[-20:]
        }

    # 5. Summary
    operation_counts = {}
    for entry in significant_entries:
        op = entry.get('operation', 'unknown')
        operation_counts[op] = operation_counts.get(op, 0) + 1

    all_dates = set(entries_by_date.keys())
    sessions = set(e.get('session_id', '') for e in significant_entries if e.get('session_id'))

    context['summary'] = {
        'date_range': {
            'from': min(all_dates) if all_dates else None,
            'to': max(all_dates) if all_dates else None,
            'days_with_activity': len(all_dates)
        },
        'total_significant_entries': len(significant_entries),
        'operation_counts': operation_counts,
        'unique_sessions': len(sessions),
        'has_recent_activity': any(
            d >= (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            for d in all_dates
        ) if all_dates else False
    }

    return context


# =========================================================================
# CLI Interface
# =========================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "--status":
            writer = get_memory_writer()
            print(f"Memory directory: {MEMORY_DIR}")
            print(f"YAML available: {YAML_AVAILABLE}")
            print(f"Session state exists: {SESSION_STATE_FILE.exists()}")
            print(f"Work log exists: {WORK_LOG_FILE.exists()}")
            print(f"Planned tasks exists: {PLANNED_TASKS_FILE.exists()}")
            print(f"Backups: {len(list(BACKUP_DIR.glob('*.yaml')))}")
            print(f"Archives: {len(list(ARCHIVE_DIR.glob('*.yaml')))}")
            print(f"Corrupt files: {len(list(CORRUPT_DIR.glob('*')))}")

        elif cmd == "--context":
            project = sys.argv[2] if len(sys.argv) > 2 else None
            context = get_integrated_context(project=project)
            print(json.dumps(context, indent=2, default=str))

        elif cmd == "--project-context":
            if len(sys.argv) < 3:
                print("Usage: memory_writer.py --project-context <project_path>")
                sys.exit(1)
            project_path = sys.argv[2]
            context = get_comprehensive_project_context(project_path)
            print(json.dumps(context, indent=2, default=str))

        else:
            print(f"Unknown command: {cmd}")
            print("Available commands: --status, --context, --project-context")
    else:
        print("Memory Writer - Enhanced memory system for Claude Code")
        print("Usage: memory_writer.py <command>")
        print("Commands: --status, --context [project], --project-context <path>")
