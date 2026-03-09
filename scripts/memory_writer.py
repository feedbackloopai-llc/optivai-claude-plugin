#!/usr/bin/env python3
"""
Memory Writer Module for Claude Code

Provides deterministic, hook-driven memory management for session persistence
and recovery. Updates 4 YAML files automatically based on hook events.

Memory Files:
- session_state.yaml: Current session context and focus
- planned_tasks.yaml: Tasks synced from TodoWrite operations
- work_log.yaml: Chronological action history (last 500 entries)
- recovery_checkpoint.yaml: Crash recovery context

Quarterly Rolloff:
- Entries older than 3 months are archived to quarterly files
- Archives stored in ~/.claude/gz-observability-memory/archive/
- Cleanup runs automatically on startup and periodically

Location: ~/.claude/gz-observability-memory/
"""

import os
import time
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Set
from collections import OrderedDict

# Try to import yaml, fall back to json if not available
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    import json
    YAML_AVAILABLE = False


# Configuration
MEMORY_DIR = Path.home() / ".claude" / "gz-observability-memory"
ARCHIVE_DIR = MEMORY_DIR / "archive"
BACKUP_DIR = MEMORY_DIR / "backups"
CORRUPT_DIR = MEMORY_DIR / "corrupt"
MAX_WORK_LOG_ENTRIES = 500
MAX_COMPLETED_TASKS = 50
CHECKPOINT_INTERVAL = 50  # Update checkpoint every N operations
MAX_BACKUPS_PER_FILE = 5  # Keep last N backups of each file

# Quarterly Rolloff Configuration
RETENTION_DAYS = 90  # 3 months / 1 quarter
CLEANUP_INTERVAL_HOURS = 24  # Run cleanup once per day
LAST_CLEANUP_FILE = MEMORY_DIR / ".last_cleanup"

# File paths
SESSION_STATE_FILE = MEMORY_DIR / "session_state.yaml"
PLANNED_TASKS_FILE = MEMORY_DIR / "planned_tasks.yaml"
WORK_LOG_FILE = MEMORY_DIR / "work_log.yaml"
RECOVERY_CHECKPOINT_FILE = MEMORY_DIR / "recovery_checkpoint.yaml"

# Critical files that should never lose data
CRITICAL_FILES = {WORK_LOG_FILE, PLANNED_TASKS_FILE}


def get_quarter_string(dt: datetime = None) -> str:
    """Get quarter string like '2025Q1' for a given datetime."""
    if dt is None:
        dt = datetime.now()
    quarter = (dt.month - 1) // 3 + 1
    return f"{dt.year}Q{quarter}"


def parse_iso_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO format timestamp string to datetime."""
    if not ts:
        return None
    try:
        # Handle various ISO formats
        ts = ts.replace('Z', '+00:00')
        if '+' in ts:
            # Has timezone
            return datetime.fromisoformat(ts)
        else:
            # No timezone, assume UTC
            return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class MemoryWriter:
    """
    Manages persistent YAML memory files for Claude Code session tracking.

    This class provides deterministic memory updates triggered by hooks:
    - pre_tool_use.py calls on_tool_use()
    - user_prompt_submit.py calls on_user_prompt()
    """

    def __init__(self):
        """Initialize the memory writer, creating directory if needed."""
        self.memory_dir = MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Create archive, backup, and corrupt directories
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        CORRUPT_DIR.mkdir(parents=True, exist_ok=True)

        # Error log for debugging
        self.error_log = self.memory_dir / "memory_errors.log"

        # Track files with load errors to prevent repeated overwrites
        # Must be initialized before _load_operation_count() which calls _load_yaml()
        self._corrupted_files = set()

        # Operation counter for checkpoint timing
        self._operation_count = self._load_operation_count()

        # Run cleanup if needed (daily)
        try:
            self.run_cleanup_if_needed()
        except Exception:
            pass  # Don't fail initialization on cleanup errors

    def _log_error(self, error_msg: str):
        """Log errors without failing the hook."""
        try:
            with open(self.error_log, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"{timestamp} ERROR: {error_msg}\n")
        except:
            pass

    def _log_warning(self, warning_msg: str):
        """Log warnings without failing the hook."""
        try:
            with open(self.error_log, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"{timestamp} WARNING: {warning_msg}\n")
        except:
            pass

    def _log_info(self, info_msg: str):
        """Log info messages."""
        try:
            with open(self.error_log, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"{timestamp} INFO: {info_msg}\n")
        except:
            pass

    # =========================================================================
    # BACKUP AND RECOVERY SYSTEM (Priority 1: Prevent Data Loss)
    # =========================================================================

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """
        Create a timestamped backup of a file before modification.

        Returns the backup path if successful, None otherwise.
        """
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
            self._log_error(f"Failed to create backup of {file_path.name}: {e}")
            return None

    def _rotate_backups(self, file_stem: str):
        """Keep only the last MAX_BACKUPS_PER_FILE backups for a given file."""
        try:
            # Find all backups for this file
            pattern = f"{file_stem}_*.yaml"
            backups = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)

            # Remove oldest backups if we have too many
            while len(backups) > MAX_BACKUPS_PER_FILE:
                oldest = backups.pop(0)
                oldest.unlink()
        except Exception as e:
            self._log_error(f"Failed to rotate backups for {file_stem}: {e}")

    def _quarantine_corrupt_file(self, file_path: Path, error_msg: str) -> Optional[Path]:
        """
        Move a corrupt file to the corrupt directory for analysis.

        Returns the quarantine path if successful, None otherwise.
        """
        if not file_path.exists():
            return None

        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            corrupt_name = f"{file_path.stem}_corrupt_{timestamp}{file_path.suffix}"
            corrupt_path = CORRUPT_DIR / corrupt_name

            # Copy (not move) to preserve original for manual recovery attempts
            shutil.copy2(file_path, corrupt_path)

            # Write metadata about the corruption
            meta_path = CORRUPT_DIR / f"{file_path.stem}_corrupt_{timestamp}.meta.txt"
            with open(meta_path, 'w', encoding='utf-8') as f:
                f.write(f"Original file: {file_path}\n")
                f.write(f"Quarantined at: {datetime.now().isoformat()}\n")
                f.write(f"Error: {error_msg}\n")
                f.write(f"File size: {file_path.stat().st_size} bytes\n")

            self._log_info(f"Quarantined corrupt file: {corrupt_path}")
            return corrupt_path
        except Exception as e:
            self._log_error(f"Failed to quarantine {file_path.name}: {e}")
            return None

    def _try_recover_from_backup(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Attempt to recover data from backup files.

        Returns the recovered data if successful, None otherwise.
        """
        try:
            # Find backups for this file, newest first
            pattern = f"{file_path.stem}_*.yaml"
            backups = sorted(
                BACKUP_DIR.glob(pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            for backup_path in backups:
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if not content.strip():
                            continue
                        if YAML_AVAILABLE:
                            data = yaml.safe_load(content)
                        else:
                            data = json.loads(content)

                        if data and isinstance(data, dict):
                            self._log_info(f"Recovered {file_path.name} from backup: {backup_path.name}")
                            return data
                except Exception:
                    continue  # Try next backup

            return None
        except Exception as e:
            self._log_error(f"Backup recovery failed for {file_path.name}: {e}")
            return None

    def _validate_data_structure(self, file_path: Path, data: Dict[str, Any]) -> bool:
        """
        Validate that loaded data has expected structure.

        Returns True if valid, False otherwise.
        """
        if not isinstance(data, dict):
            return False

        # File-specific validation
        if file_path == WORK_LOG_FILE:
            # Work log must have entries list
            if 'entries' in data and not isinstance(data.get('entries'), list):
                return False
        elif file_path == PLANNED_TASKS_FILE:
            # Planned tasks must have proper structure
            for key in ['pending', 'in_progress', 'completed']:
                if key in data and not isinstance(data.get(key), list):
                    return False

        return True

    def _load_yaml_safe(self, file_path: Path) -> Tuple[Dict[str, Any], bool]:
        """
        Safely load YAML file with backup recovery on failure.

        Returns tuple of (data, was_recovered).
        - If file doesn't exist: ({}, False)
        - If loaded successfully: (data, False)
        - If recovered from backup: (data, True)
        - If unrecoverable: ({}, False) but file is NOT modified

        CRITICAL: Never returns empty dict for critical files if data exists.
        """
        if not file_path.exists():
            return ({}, False)

        # Don't try to load files we've already marked as corrupted this session
        if file_path in self._corrupted_files:
            self._log_warning(f"Skipping corrupted file {file_path.name} - marked corrupt this session")
            return ({}, False)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    return ({}, False)

                if YAML_AVAILABLE:
                    data = yaml.safe_load(content)
                else:
                    data = json.loads(content)

                data = data or {}

                # Validate structure
                if not self._validate_data_structure(file_path, data):
                    raise ValueError(f"Invalid data structure in {file_path.name}")

                return (data, False)

        except Exception as e:
            error_msg = f"Failed to load {file_path.name}: {e}"
            self._log_error(error_msg)

            # For critical files, try recovery before giving up
            if file_path in CRITICAL_FILES:
                # Quarantine the corrupt file
                self._quarantine_corrupt_file(file_path, str(e))

                # Try to recover from backup
                recovered = self._try_recover_from_backup(file_path)
                if recovered:
                    # Write recovered data back to main file
                    try:
                        self._save_yaml_direct(file_path, recovered)
                        self._log_info(f"Restored {file_path.name} from backup")
                        return (recovered, True)
                    except Exception as restore_err:
                        self._log_error(f"Failed to restore {file_path.name}: {restore_err}")

                # Mark as corrupted to prevent repeated recovery attempts
                self._corrupted_files.add(file_path)
                self._log_error(f"CRITICAL: {file_path.name} is corrupt and unrecoverable from backups")

            return ({}, False)

    def _save_yaml_direct(self, file_path: Path, data: Dict[str, Any]):
        """Save data directly without backup (used for recovery)."""
        with open(file_path, 'w', encoding='utf-8') as f:
            if YAML_AVAILABLE:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            else:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_yaml(self, file_path: Path) -> Dict[str, Any]:
        """
        Load YAML file with safety features for critical files.

        For critical files (work_log, planned_tasks):
        - Attempts recovery from backup on corruption
        - Quarantines corrupt files for analysis
        - Never silently loses data

        For non-critical files:
        - Returns empty dict on error (legacy behavior)
        """
        # Use safe loading for all files
        data, was_recovered = self._load_yaml_safe(file_path)

        if was_recovered:
            self._log_info(f"Using recovered data for {file_path.name}")

        return data

    def _save_yaml(self, file_path: Path, data: Dict[str, Any]):
        """
        Save data to YAML file with backup for critical files.

        For critical files:
        - Creates backup before overwriting
        - Validates data structure before saving
        """
        try:
            # Create backup for critical files before modifying
            if file_path in CRITICAL_FILES and file_path.exists():
                self._create_backup(file_path)

            # Validate data before saving
            if not self._validate_data_structure(file_path, data):
                self._log_error(f"Refusing to save invalid data structure to {file_path.name}")
                return

            with open(file_path, 'w', encoding='utf-8') as f:
                if YAML_AVAILABLE:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                else:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log_error(f"Failed to save {file_path.name}: {e}")

    def _load_operation_count(self) -> int:
        """Load operation count from session state."""
        state = self._load_yaml(SESSION_STATE_FILE)
        return state.get('operation_count', 0)

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def _get_local_time(self) -> str:
        """Get current local time as string."""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # =========================================================================
    # QUARTERLY ROLLOFF / ARCHIVE MANAGEMENT
    # =========================================================================

    def _should_run_cleanup(self) -> bool:
        """Check if cleanup should run based on last cleanup time."""
        if not LAST_CLEANUP_FILE.exists():
            return True

        try:
            with open(LAST_CLEANUP_FILE, 'r') as f:
                last_cleanup_str = f.read().strip()
                last_cleanup = parse_iso_timestamp(last_cleanup_str)
                if last_cleanup is None:
                    return True

                hours_since = (datetime.now(timezone.utc) - last_cleanup).total_seconds() / 3600
                return hours_since >= CLEANUP_INTERVAL_HOURS
        except Exception:
            return True

    def _mark_cleanup_done(self):
        """Record that cleanup was just performed."""
        try:
            with open(LAST_CLEANUP_FILE, 'w') as f:
                f.write(self._get_timestamp())
        except Exception as e:
            self._log_error(f"Failed to mark cleanup done: {e}")

    def _archive_work_log_entries(self) -> int:
        """
        Archive work log entries older than RETENTION_DAYS to quarterly files.

        Returns number of entries archived.
        """
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        log_data = self._load_yaml(WORK_LOG_FILE)
        entries = log_data.get('entries', [])

        if not entries:
            return 0

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        entries_to_keep = []
        entries_to_archive = {}  # Grouped by quarter

        for entry in entries:
            entry_time = parse_iso_timestamp(entry.get('timestamp', ''))
            if entry_time is None:
                # Can't parse timestamp, keep it
                entries_to_keep.append(entry)
                continue

            if entry_time < cutoff_date:
                # Archive this entry
                quarter = get_quarter_string(entry_time)
                if quarter not in entries_to_archive:
                    entries_to_archive[quarter] = []
                entries_to_archive[quarter].append(entry)
            else:
                entries_to_keep.append(entry)

        # Write archived entries to quarterly files
        archived_count = 0
        for quarter, archived_entries in entries_to_archive.items():
            archive_file = ARCHIVE_DIR / f"work_log_{quarter}.yaml"

            # Load existing archive if any
            existing_archive = self._load_yaml(archive_file)
            existing_entries = existing_archive.get('entries', [])

            # Append new archived entries
            existing_entries.extend(archived_entries)
            archived_count += len(archived_entries)

            archive_data = {
                'quarter': quarter,
                'archived_at': self._get_timestamp(),
                'entry_count': len(existing_entries),
                'entries': existing_entries
            }
            self._save_yaml(archive_file, archive_data)

        # Update work log with only recent entries
        if archived_count > 0:
            log_data['entries'] = entries_to_keep
            log_data['entry_count'] = len(entries_to_keep)
            log_data['last_cleanup'] = self._get_timestamp()
            log_data['last_archived_count'] = archived_count
            self._save_yaml(WORK_LOG_FILE, log_data)

        return archived_count

    def _cleanup_old_session_files(self) -> int:
        """
        Clean up session state snapshots older than RETENTION_DAYS.

        Returns number of files cleaned.
        """
        # For now, we only manage work_log archival
        # Session state and recovery checkpoints are single-file, not log-based
        # They get overwritten naturally
        return 0

    def run_cleanup_if_needed(self) -> Tuple[bool, int]:
        """
        Run cleanup if enough time has passed since last cleanup.

        Returns:
            Tuple of (cleanup_ran: bool, entries_archived: int)
        """
        if not self._should_run_cleanup():
            return (False, 0)

        try:
            archived_count = self._archive_work_log_entries()
            self._mark_cleanup_done()
            return (True, archived_count)
        except Exception as e:
            self._log_error(f"Cleanup failed: {e}")
            return (False, 0)

    def get_archive_summary(self) -> Dict[str, Any]:
        """Get summary of archived data."""
        summary = {
            'archive_dir': str(ARCHIVE_DIR),
            'retention_days': RETENTION_DAYS,
            'archives': []
        }

        if not ARCHIVE_DIR.exists():
            return summary

        for archive_file in sorted(ARCHIVE_DIR.glob('work_log_*.yaml')):
            archive_data = self._load_yaml(archive_file)
            summary['archives'].append({
                'file': archive_file.name,
                'quarter': archive_data.get('quarter', 'unknown'),
                'entry_count': archive_data.get('entry_count', 0),
                'archived_at': archive_data.get('archived_at', '')
            })

        return summary

    # =========================================================================
    # SESSION STATE MANAGEMENT
    # =========================================================================

    def update_session_state(self,
                             operation: str,
                             project: str,
                             cwd: str,
                             session_id: str,
                             focus: Optional[str] = None,
                             subagent_context: Optional[Dict] = None):
        """
        Update session_state.yaml with current context.

        Called on every operation to track:
        - Current project/directory
        - Operation count
        - Last activity timestamp
        - Current focus area
        - Subagent context if within a subagent
        """
        state = self._load_yaml(SESSION_STATE_FILE)

        self._operation_count += 1

        state.update({
            'session_id': session_id,
            'project': project,
            'cwd': cwd,
            'last_operation': operation,
            'last_activity': self._get_timestamp(),
            'last_activity_local': self._get_local_time(),
            'operation_count': self._operation_count,
            'started_at': state.get('started_at', self._get_timestamp()),
        })

        # Update focus if provided
        if focus:
            state['current_focus'] = focus

        # Track subagent context
        if subagent_context and subagent_context.get('is_subagent'):
            state['subagent'] = {
                'type': subagent_context.get('executing_subagent'),
                'id': subagent_context.get('executing_subagent_id'),
                'depth': subagent_context.get('subagent_depth', 0),
                'lineage': subagent_context.get('subagent_lineage', [])
            }
        elif 'subagent' in state:
            del state['subagent']

        self._save_yaml(SESSION_STATE_FILE, state)

    # =========================================================================
    # PLANNED TASKS MANAGEMENT (synced with TodoWrite)
    # =========================================================================

    def sync_planned_tasks(self, todos: List[Dict[str, Any]]):
        """
        Sync planned_tasks.yaml with TodoWrite operations.

        Called when TodoWrite tool is used. Maintains 1:1 sync with
        the TodoWrite tool's state.
        """
        # Group tasks by status
        pending = []
        in_progress = []
        completed = []

        for todo in todos:
            task_entry = {
                'content': todo.get('content', ''),
                'active_form': todo.get('activeForm', ''),
                'added_at': self._get_timestamp()
            }

            status = todo.get('status', 'pending')
            if status == 'pending':
                pending.append(task_entry)
            elif status == 'in_progress':
                in_progress.append(task_entry)
            elif status == 'completed':
                completed.append(task_entry)

        # Keep only last N completed tasks
        completed = completed[-MAX_COMPLETED_TASKS:]

        tasks_data = {
            'last_synced': self._get_timestamp(),
            'pending': pending,
            'in_progress': in_progress,
            'completed': completed,
            'summary': {
                'total': len(todos),
                'pending_count': len(pending),
                'in_progress_count': len(in_progress),
                'completed_count': len(completed)
            }
        }

        self._save_yaml(PLANNED_TASKS_FILE, tasks_data)

    def clear_planned_tasks(self):
        """Clear all planned tasks (called on new user prompt if appropriate)."""
        tasks_data = {
            'last_synced': self._get_timestamp(),
            'pending': [],
            'in_progress': [],
            'completed': [],
            'summary': {
                'total': 0,
                'pending_count': 0,
                'in_progress_count': 0,
                'completed_count': 0
            }
        }
        self._save_yaml(PLANNED_TASKS_FILE, tasks_data)

    # =========================================================================
    # WORK LOG MANAGEMENT
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

        Called for significant operations:
        - File writes/edits
        - Task spawns
        - User prompts
        - Bash commands that modify things

        Args:
            operation: The operation type (e.g., 'write', 'bash', 'user_prompt')
            description: Human-readable description of the operation
            details: Optional dict with operation-specific details
            is_significant: Whether this is significant enough to log
            project: Project name (extracted from cwd directory name)
            cwd: Current working directory path

        Keeps last MAX_WORK_LOG_ENTRIES entries.
        """
        if not is_significant:
            return

        log_data = self._load_yaml(WORK_LOG_FILE)
        entries = log_data.get('entries', [])

        entry = {
            'timestamp': self._get_timestamp(),
            'local_time': self._get_local_time(),
            'operation': operation,
            'description': description[:200] if description else '',
        }

        # Add project context (Priority 2: Always include project info)
        if project:
            entry['project'] = project
        if cwd:
            entry['cwd'] = cwd

        if details:
            # Include key details, truncating long values
            entry['details'] = {}
            for key, value in details.items():
                if key.startswith('_'):  # Skip internal flags
                    continue
                if isinstance(value, str) and len(value) > 100:
                    entry['details'][key] = value[:100] + '...'
                elif isinstance(value, (list, dict)):
                    entry['details'][key] = value
                else:
                    entry['details'][key] = value

        entries.append(entry)

        # Keep only last N entries
        entries = entries[-MAX_WORK_LOG_ENTRIES:]

        log_data = {
            'last_updated': self._get_timestamp(),
            'entry_count': len(entries),
            'entries': entries
        }

        self._save_yaml(WORK_LOG_FILE, log_data)

    # =========================================================================
    # RECOVERY CHECKPOINT MANAGEMENT
    # =========================================================================

    def update_recovery_checkpoint(self,
                                   operation: str,
                                   context: Dict[str, Any],
                                   force: bool = False):
        """
        Update recovery_checkpoint.yaml for crash recovery.

        Called:
        - Every CHECKPOINT_INTERVAL operations
        - Before Task launches (force=True)
        - On user prompts (force=True)

        Provides enough context to resume work after a crash.
        """
        # Only update periodically unless forced
        if not force and (self._operation_count % CHECKPOINT_INTERVAL != 0):
            return

        checkpoint = {
            'checkpoint_time': self._get_timestamp(),
            'checkpoint_time_local': self._get_local_time(),
            'operation_count': self._operation_count,
            'last_operation': operation,
            'session_id': context.get('session_id', ''),
            'project': context.get('project', ''),
            'cwd': context.get('cwd', ''),
        }

        # Include current focus if available
        state = self._load_yaml(SESSION_STATE_FILE)
        if state.get('current_focus'):
            checkpoint['current_focus'] = state['current_focus']

        # Include active tasks summary
        tasks = self._load_yaml(PLANNED_TASKS_FILE)
        if tasks.get('in_progress'):
            checkpoint['active_tasks'] = [t.get('content', '') for t in tasks['in_progress']]
        if tasks.get('pending'):
            checkpoint['pending_tasks'] = [t.get('content', '') for t in tasks['pending'][:5]]

        # Include subagent context if any
        if context.get('subagent_context'):
            checkpoint['subagent'] = context['subagent_context']

        # Include recent work for context
        log_data = self._load_yaml(WORK_LOG_FILE)
        recent = log_data.get('entries', [])[-5:]
        if recent:
            checkpoint['recent_work'] = [
                {'op': e.get('operation'), 'desc': e.get('description', '')[:50]}
                for e in recent
            ]

        self._save_yaml(RECOVERY_CHECKPOINT_FILE, checkpoint)

    # =========================================================================
    # HIGH-LEVEL HOOK HANDLERS
    # =========================================================================

    def on_tool_use(self,
                    operation: str,
                    prompt: str,
                    details: Dict[str, Any],
                    session_id: str,
                    project: str,
                    cwd: str,
                    subagent_context: Optional[Dict] = None):
        """
        Called by pre_tool_use.py on every tool invocation.

        Updates:
        - session_state.yaml (always)
        - work_log.yaml (for significant operations)
        - planned_tasks.yaml (if TodoWrite)
        - recovery_checkpoint.yaml (periodically or on Task)
        """
        try:
            # Always update session state
            self.update_session_state(
                operation=operation,
                project=project,
                cwd=cwd,
                session_id=session_id,
                subagent_context=subagent_context
            )

            # Handle TodoWrite specially - sync tasks
            if operation == 'todo_write' and 'todos' in details:
                # details might have 'todos' from the raw tool input
                pass  # Handled separately below

            # Determine if this is a significant operation for work log
            significant_ops = {'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write'}
            is_significant = operation in significant_ops

            # Log to work log with project context
            if is_significant:
                self.log_work_entry(
                    operation=operation,
                    description=prompt,
                    details=details,
                    is_significant=True,
                    project=project,
                    cwd=cwd
                )

            # Update checkpoint periodically or on Task launches
            force_checkpoint = (operation == 'task')
            self.update_recovery_checkpoint(
                operation=operation,
                context={
                    'session_id': session_id,
                    'project': project,
                    'cwd': cwd,
                    'subagent_context': subagent_context
                },
                force=force_checkpoint
            )

        except Exception as e:
            self._log_error(f"on_tool_use failed: {e}")

    def on_todo_write(self, todos: List[Dict[str, Any]]):
        """
        Called by pre_tool_use.py specifically for TodoWrite operations.

        Syncs planned_tasks.yaml with the TodoWrite state.
        """
        try:
            self.sync_planned_tasks(todos)
        except Exception as e:
            self._log_error(f"on_todo_write failed: {e}")

    def on_user_prompt(self,
                       prompt: str,
                       session_id: str,
                       project: str,
                       cwd: str):
        """
        Called by user_prompt_submit.py on every user message.

        Updates:
        - session_state.yaml (with focus detection)
        - work_log.yaml
        - recovery_checkpoint.yaml (always)
        """
        try:
            # Detect focus from prompt
            focus = self._detect_focus(prompt)

            # Update session state
            self.update_session_state(
                operation='user_prompt',
                project=project,
                cwd=cwd,
                session_id=session_id,
                focus=focus
            )

            # Log to work log with project context
            self.log_work_entry(
                operation='user_prompt',
                description=prompt[:200],
                details={'prompt_length': len(prompt)},
                is_significant=True,
                project=project,
                cwd=cwd
            )

            # Always checkpoint on user prompt
            self.update_recovery_checkpoint(
                operation='user_prompt',
                context={
                    'session_id': session_id,
                    'project': project,
                    'cwd': cwd
                },
                force=True
            )

        except Exception as e:
            self._log_error(f"on_user_prompt failed: {e}")

    def _detect_focus(self, prompt: str) -> Optional[str]:
        """
        Detect current focus area from user prompt.

        Looks for command patterns, project references, etc.
        """
        prompt_lower = prompt.lower()

        # Common focus areas
        focus_keywords = {
            'etl': 'ETL Pipeline Development',
            'pipeline': 'Data Pipeline',
            'postgresql': 'PostgreSQL Development',
            'sql': 'SQL Development',
            'api': 'API Development',
            'logging': 'Logging System',
            'test': 'Testing',
            'debug': 'Debugging',
            'fix': 'Bug Fixing',
            'refactor': 'Refactoring',
            'document': 'Documentation',
            'review': 'Code Review',
        }

        for keyword, focus in focus_keywords.items():
            if keyword in prompt_lower:
                return focus

        return None


# Singleton instance
_memory_writer_instance = None


def get_memory_writer() -> MemoryWriter:
    """Get singleton memory writer instance."""
    global _memory_writer_instance
    if _memory_writer_instance is None:
        _memory_writer_instance = MemoryWriter()
    return _memory_writer_instance


# Convenience functions for hook integration
def on_tool_use(operation: str, prompt: str, details: Dict,
                session_id: str, project: str, cwd: str,
                subagent_context: Optional[Dict] = None):
    """Called by pre_tool_use.py"""
    get_memory_writer().on_tool_use(
        operation, prompt, details, session_id, project, cwd, subagent_context
    )


def on_todo_write(todos: List[Dict]):
    """Called by pre_tool_use.py for TodoWrite operations"""
    get_memory_writer().on_todo_write(todos)


def on_user_prompt(prompt: str, session_id: str, project: str, cwd: str):
    """Called by user_prompt_submit.py"""
    get_memory_writer().on_user_prompt(prompt, session_id, project, cwd)


def get_archive_summary() -> Dict[str, Any]:
    """Get summary of archived data for inspection."""
    return get_memory_writer().get_archive_summary()


def run_cleanup() -> Tuple[bool, int]:
    """Manually trigger cleanup. Returns (ran, entries_archived)."""
    return get_memory_writer().run_cleanup_if_needed()


# =============================================================================
# INTEGRATED CONTEXT ACCESS (Priority 4: Architecture Improvement)
# =============================================================================

# Common search paths for hook logs
HOOK_LOG_SEARCH_PATHS = [
    Path.home() / ".claude" / "logs",  # Global logs
    Path.home() / "Documents",          # Documents folder and subprojects
    Path.home() / "Projects",           # Common projects folder
    Path.home() / "Code",               # Common code folder
]


def find_hook_log_files(project_filter: str = None,
                        from_date: str = None,
                        to_date: str = None,
                        search_paths: List[Path] = None) -> List[Path]:
    """
    Find hook log files across all known locations.

    Args:
        project_filter: Optional project name to filter by
        from_date: Optional start date (YYYY-MM-DD)
        to_date: Optional end date (YYYY-MM-DD)
        search_paths: Custom search paths (defaults to HOOK_LOG_SEARCH_PATHS)

    Returns:
        List of Path objects to log files, sorted by name (date)
    """
    if search_paths is None:
        search_paths = HOOK_LOG_SEARCH_PATHS

    log_files = set()

    for base_path in search_paths:
        if not base_path.exists():
            continue

        try:
            for claude_dir in base_path.rglob(".claude/logs"):
                if claude_dir.is_dir():
                    # Check if this matches project filter
                    if project_filter:
                        parts = claude_dir.parts
                        if '.claude' in parts:
                            claude_idx = parts.index('.claude')
                            if claude_idx > 0:
                                dir_project = parts[claude_idx - 1]
                                if project_filter.lower() not in dir_project.lower():
                                    continue

                    for log_file in claude_dir.glob("agent-activity-*.log"):
                        if log_file.is_file():
                            # Apply date filter
                            if from_date or to_date:
                                file_date = log_file.stem.replace("agent-activity-", "")
                                if from_date and file_date < from_date:
                                    continue
                                if to_date and file_date > to_date:
                                    continue
                            log_files.add(log_file)
        except PermissionError:
            continue

    return sorted(log_files, key=lambda p: p.name)


def parse_hook_log_entries(log_file: Path,
                           limit: int = None,
                           operation_filter: Set[str] = None) -> List[Dict[str, Any]]:
    """
    Parse entries from a hook log file.

    Args:
        log_file: Path to the log file
        limit: Maximum entries to return (from end of file)
        operation_filter: Set of operations to include (None = all)

    Returns:
        List of parsed log entries
    """
    import json
    entries = []

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Process from end if we have a limit
        if limit:
            lines = lines[-limit * 3:]  # Read extra to account for filtering

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)

                # Apply operation filter
                if operation_filter:
                    if entry.get('operation') not in operation_filter:
                        continue

                entries.append(entry)
            except json.JSONDecodeError:
                continue

        # Apply limit after filtering
        if limit and len(entries) > limit:
            entries = entries[-limit:]

    except Exception:
        pass

    return entries


def get_integrated_context(project: str = None,
                           days_back: int = 7,
                           max_entries: int = 100) -> Dict[str, Any]:
    """
    Get integrated context from both memory system and hook logs.

    This provides a unified view for session recovery by combining:
    - Memory system: session_state, planned_tasks, work_log, recovery_checkpoint
    - Hook logs: Recent activity from per-project logs

    Args:
        project: Optional project filter
        days_back: How many days of history to include
        max_entries: Maximum entries per source

    Returns:
        Integrated context dictionary with all sources
    """
    writer = get_memory_writer()
    context = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'project_filter': project,
        'sources': {}
    }

    # 1. Memory System Data
    context['sources']['memory_system'] = {
        'session_state': writer._load_yaml(SESSION_STATE_FILE),
        'planned_tasks': writer._load_yaml(PLANNED_TASKS_FILE),
        'recovery_checkpoint': writer._load_yaml(RECOVERY_CHECKPOINT_FILE),
    }

    # Work log (filtered by project if specified)
    work_log = writer._load_yaml(WORK_LOG_FILE)
    work_entries = work_log.get('entries', [])
    if project:
        work_entries = [e for e in work_entries if e.get('project', '').lower() == project.lower()]
    work_entries = work_entries[-max_entries:]
    context['sources']['memory_system']['work_log'] = {
        'entry_count': len(work_entries),
        'entries': work_entries
    }

    # 2. Hook Log Data
    from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    hook_files = find_hook_log_files(project_filter=project, from_date=from_date)

    hook_entries = []
    projects_found = set()

    for log_file in hook_files:
        entries = parse_hook_log_entries(
            log_file,
            limit=max_entries // max(len(hook_files), 1),
            operation_filter={'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write'}
        )
        for entry in entries:
            if entry.get('project'):
                projects_found.add(entry['project'])
        hook_entries.extend(entries)

    # Sort by timestamp and limit
    hook_entries.sort(key=lambda e: e.get('timestamp', ''))
    hook_entries = hook_entries[-max_entries:]

    context['sources']['hook_logs'] = {
        'files_found': len(hook_files),
        'entry_count': len(hook_entries),
        'projects': list(projects_found),
        'entries': hook_entries
    }

    # 3. Summary
    all_projects = set(projects_found)
    for entry in work_entries:
        if entry.get('project'):
            all_projects.add(entry['project'])

    context['summary'] = {
        'total_memory_entries': len(work_entries),
        'total_hook_entries': len(hook_entries),
        'projects': sorted(all_projects),
        'hook_files': len(hook_files),
        'date_range': {
            'from': from_date,
            'to': datetime.now().strftime('%Y-%m-%d')
        }
    }

    return context


def get_project_activity_summary(project: str, days_back: int = 30) -> Dict[str, Any]:
    """
    Get activity summary for a specific project.

    Args:
        project: Project name
        days_back: Days of history to include

    Returns:
        Summary with recent activity, task counts, and key files
    """
    context = get_integrated_context(project=project, days_back=days_back, max_entries=200)

    # Combine entries from both sources
    all_entries = []

    # Memory system entries
    for entry in context['sources']['memory_system']['work_log'].get('entries', []):
        all_entries.append({
            'source': 'memory',
            'timestamp': entry.get('timestamp', ''),
            'operation': entry.get('operation', ''),
            'description': entry.get('description', '')[:100],
            'details': entry.get('details', {})
        })

    # Hook log entries
    for entry in context['sources']['hook_logs'].get('entries', []):
        all_entries.append({
            'source': 'hooks',
            'timestamp': entry.get('timestamp', ''),
            'operation': entry.get('operation', ''),
            'description': entry.get('prompt', '')[:100],
            'details': entry.get('details', {})
        })

    # Dedupe and sort
    seen = set()
    unique_entries = []
    for entry in all_entries:
        key = f"{entry['timestamp']}|{entry['operation']}|{entry['description'][:30]}"
        if key not in seen:
            seen.add(key)
            unique_entries.append(entry)

    unique_entries.sort(key=lambda e: e['timestamp'])

    # Calculate stats
    operation_counts = {}
    for entry in unique_entries:
        op = entry['operation']
        operation_counts[op] = operation_counts.get(op, 0) + 1

    # Find touched files from details
    files_touched = set()
    for entry in unique_entries:
        details = entry.get('details', {})
        if details.get('file_path'):
            files_touched.add(details['file_path'])
        if details.get('target_file'):
            files_touched.add(details['target_file'])

    return {
        'project': project,
        'date_range': context['summary']['date_range'],
        'total_entries': len(unique_entries),
        'operation_counts': operation_counts,
        'files_touched': sorted(files_touched)[:20],  # Top 20 files
        'recent_entries': unique_entries[-10:],  # Last 10
        'sources': {
            'memory': context['summary']['total_memory_entries'],
            'hooks': context['summary']['total_hook_entries']
        }
    }


# =============================================================================
# ENHANCED PROJECT CONTEXT (Direct Path Search, Extended Lookback, File History)
# =============================================================================

def get_project_hook_logs(project_path: str, days_back: int = 90) -> List[Dict[str, Any]]:
    """
    Get ALL hook log entries from a specific project's .claude/logs/ directory.

    This searches the project path directly rather than relying on global search,
    and looks back further (90 days by default) to find historical context.

    Args:
        project_path: Absolute path to the project directory
        days_back: How many days of history to include (default: 90)

    Returns:
        List of all parsed log entries from this project
    """
    import json

    project_path = Path(project_path).resolve()
    project_logs_dir = project_path / ".claude" / "logs"

    if not project_logs_dir.exists():
        return []

    # Calculate date cutoff
    from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    all_entries = []
    log_files = sorted(project_logs_dir.glob("agent-activity-*.log"))

    for log_file in log_files:
        # Apply date filter
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


def search_hook_logs_by_content(project_path: str,
                                 keywords: List[str],
                                 days_back: int = 90,
                                 case_sensitive: bool = False) -> List[Dict[str, Any]]:
    """
    Search hook log content for specific keywords/topics.

    Args:
        project_path: Absolute path to the project directory
        keywords: List of keywords to search for in prompts/descriptions
        days_back: How many days of history to search (default: 90)
        case_sensitive: Whether search is case-sensitive (default: False)

    Returns:
        List of entries matching any of the keywords
    """
    all_entries = get_project_hook_logs(project_path, days_back)

    if not case_sensitive:
        keywords = [k.lower() for k in keywords]

    matching_entries = []

    for entry in all_entries:
        # Search in prompt/description
        prompt = entry.get('prompt', '')
        if not case_sensitive:
            prompt = prompt.lower()

        # Check if any keyword matches
        for keyword in keywords:
            if keyword in prompt:
                entry['_matched_keyword'] = keyword
                matching_entries.append(entry)
                break

        # Also check in details if present
        details = entry.get('details', {})
        if details:
            details_str = str(details)
            if not case_sensitive:
                details_str = details_str.lower()
            for keyword in keywords:
                if keyword in details_str:
                    if entry not in matching_entries:
                        entry['_matched_keyword'] = keyword
                        matching_entries.append(entry)
                    break

    return matching_entries


def get_file_history(project_path: str,
                     file_pattern: str = None,
                     days_back: int = 90) -> Dict[str, Any]:
    """
    Get history of file operations in a project.

    Args:
        project_path: Absolute path to the project directory
        file_pattern: Optional pattern to filter files (e.g., "memory_writer.py", "*.sql")
        days_back: How many days of history to include (default: 90)

    Returns:
        Dictionary with file history organized by file path
    """
    import fnmatch

    all_entries = get_project_hook_logs(project_path, days_back)

    # Track operations by file
    file_operations = {}  # file_path -> list of operations

    for entry in all_entries:
        operation = entry.get('operation', '')
        details = entry.get('details', {})

        # Extract file paths from various sources
        file_paths = set()

        # From details
        if details.get('file_path'):
            file_paths.add(details['file_path'])
        if details.get('target_file'):
            file_paths.add(details['target_file'])
        if details.get('path'):
            file_paths.add(details['path'])

        # From prompt (for edit/write operations)
        if operation in ('write', 'edit', 'read') and not file_paths:
            # Try to extract file path from prompt
            prompt = entry.get('prompt', '')
            # Common patterns: "file: /path/to/file" or just "/path/to/file.ext"
            import re
            path_matches = re.findall(r'(/[^\s]+\.\w+)', prompt)
            for match in path_matches:
                if len(match) > 5:  # Minimum valid path
                    file_paths.add(match)

        # Apply file pattern filter
        for file_path in file_paths:
            if file_pattern:
                filename = Path(file_path).name
                if not fnmatch.fnmatch(filename, file_pattern) and file_pattern not in file_path:
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

    # Sort operations by timestamp for each file
    for file_path in file_operations:
        file_operations[file_path].sort(key=lambda e: e['timestamp'])

    # Calculate summary stats
    files_by_activity = sorted(
        file_operations.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    return {
        'project_path': str(project_path),
        'days_back': days_back,
        'file_pattern': file_pattern,
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

    This is the main function for /prime-agent when given a project path.
    It combines:
    - Direct project hook log search (all history in that project)
    - Memory system data filtered to project
    - File history tracking
    - Optional keyword search

    Args:
        project_path: Absolute path to the project directory
        days_back: How many days of history (default: 90)
        keywords: Optional keywords to highlight relevant entries
        include_file_history: Whether to include file history analysis

    Returns:
        Comprehensive context dictionary
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

    # Filter to significant operations
    significant_ops = {'write', 'edit', 'task', 'bash', 'user_prompt', 'todo_write'}
    significant_entries = [e for e in hook_entries if e.get('operation') in significant_ops]

    # Group by date for summary
    entries_by_date = {}
    for entry in significant_entries:
        date = entry.get('_file_date', 'unknown')
        if date not in entries_by_date:
            entries_by_date[date] = []
        entries_by_date[date].append(entry)

    context['sources']['project_hooks'] = {
        'total_entries': len(hook_entries),
        'significant_entries': len(significant_entries),
        'dates_with_activity': sorted(entries_by_date.keys()),
        'entries_by_date_count': {d: len(e) for d, e in entries_by_date.items()},
        'recent_entries': significant_entries[-20:]  # Last 20 significant entries
    }

    # 2. Memory system data (filtered to this project)
    writer = get_memory_writer()

    # Session state
    session_state = writer._load_yaml(SESSION_STATE_FILE)
    context['sources']['memory_system'] = {
        'session_state': session_state,
        'planned_tasks': writer._load_yaml(PLANNED_TASKS_FILE),
        'recovery_checkpoint': writer._load_yaml(RECOVERY_CHECKPOINT_FILE)
    }

    # Work log filtered to this project
    work_log = writer._load_yaml(WORK_LOG_FILE)
    work_entries = work_log.get('entries', [])
    project_work_entries = [
        e for e in work_entries
        if e.get('project', '').lower() == project_name.lower()
        or project_name.lower() in e.get('cwd', '').lower()
    ]
    context['sources']['memory_system']['work_log'] = {
        'total_entries': len(project_work_entries),
        'entries': project_work_entries[-30:]  # Last 30 for this project
    }

    # 3. File history (if requested)
    if include_file_history:
        file_history = get_file_history(str(project_path), days_back=days_back)
        context['sources']['file_history'] = {
            'total_files_touched': file_history['total_files_touched'],
            'total_operations': file_history['total_operations'],
            'most_active_files': file_history['most_active_files']
        }

    # 4. Keyword matches (if provided)
    if keywords:
        keyword_matches = search_hook_logs_by_content(
            str(project_path), keywords, days_back
        )
        context['sources']['keyword_matches'] = {
            'keywords': keywords,
            'match_count': len(keyword_matches),
            'matches': keyword_matches[-20:]  # Last 20 matches
        }

    # 5. Build summary
    all_dates = set(entries_by_date.keys())

    # Operation breakdown
    operation_counts = {}
    for entry in significant_entries:
        op = entry.get('operation', 'unknown')
        operation_counts[op] = operation_counts.get(op, 0) + 1

    # Session breakdown
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


if __name__ == "__main__":
    # Test the memory writer
    print("=== Memory Writer Test ===")

    writer = MemoryWriter()
    print(f"Memory directory: {writer.memory_dir}")
    print(f"YAML available: {YAML_AVAILABLE}")

    # Test session state update
    writer.update_session_state(
        operation='test',
        project='test-project',
        cwd='/tmp/test',
        session_id='test-session-123'
    )
    print("Session state updated")

    # Test work log
    writer.log_work_entry(
        operation='test',
        description='Test work log entry',
        details={'test': True}
    )
    print("Work log entry added")

    # Test planned tasks
    writer.sync_planned_tasks([
        {'content': 'Task 1', 'status': 'pending', 'activeForm': 'Working on Task 1'},
        {'content': 'Task 2', 'status': 'in_progress', 'activeForm': 'Working on Task 2'},
    ])
    print("Planned tasks synced")

    # Test checkpoint
    writer.update_recovery_checkpoint(
        operation='test',
        context={'session_id': 'test-session-123', 'project': 'test-project', 'cwd': '/tmp/test'},
        force=True
    )
    print("Recovery checkpoint updated")

    print("\n=== Files Created ===")
    for f in [SESSION_STATE_FILE, PLANNED_TASKS_FILE, WORK_LOG_FILE, RECOVERY_CHECKPOINT_FILE]:
        if f.exists():
            print(f"  {f.name}: {f.stat().st_size} bytes")

    # Test archive functionality
    print("\n=== Archive Configuration ===")
    print(f"Archive directory: {ARCHIVE_DIR}")
    print(f"Retention days: {RETENTION_DAYS}")
    print(f"Cleanup interval: {CLEANUP_INTERVAL_HOURS} hours")

    # Get archive summary
    summary = writer.get_archive_summary()
    print(f"\n=== Archive Summary ===")
    print(f"Archive directory: {summary['archive_dir']}")
    if summary['archives']:
        for archive in summary['archives']:
            print(f"  {archive['file']}: {archive['entry_count']} entries ({archive['quarter']})")
    else:
        print("  No archives yet")

    # Test cleanup
    print("\n=== Cleanup Test ===")
    ran, count = writer.run_cleanup_if_needed()
    print(f"Cleanup ran: {ran}, entries archived: {count}")
