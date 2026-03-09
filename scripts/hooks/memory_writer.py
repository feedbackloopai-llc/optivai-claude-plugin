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
from typing import Dict, Any, Optional, List, Tuple
from collections import OrderedDict

# Import secret redaction (fail silently if not available)
try:
    from redact_secrets import redact_secrets, redact_dict
except ImportError:
    # Fallback: no redaction if module unavailable
    def redact_secrets(text):
        return text
    def redact_dict(data, max_depth=10):
        return data

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
MAX_WORK_LOG_ENTRIES = 500
MAX_COMPLETED_TASKS = 50
CHECKPOINT_INTERVAL = 50  # Update checkpoint every N operations

# Quarterly Rolloff Configuration
RETENTION_DAYS = 90  # 3 months / 1 quarter
CLEANUP_INTERVAL_HOURS = 24  # Run cleanup once per day
LAST_CLEANUP_FILE = MEMORY_DIR / ".last_cleanup"

# File paths
SESSION_STATE_FILE = MEMORY_DIR / "session_state.yaml"
PLANNED_TASKS_FILE = MEMORY_DIR / "planned_tasks.yaml"
WORK_LOG_FILE = MEMORY_DIR / "work_log.yaml"
RECOVERY_CHECKPOINT_FILE = MEMORY_DIR / "recovery_checkpoint.yaml"


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

        # Create archive directory
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Operation counter for checkpoint timing
        self._operation_count = self._load_operation_count()

        # Error log for debugging
        self.error_log = self.memory_dir / "memory_errors.log"

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

    def _load_yaml(self, file_path: Path) -> Dict[str, Any]:
        """Load YAML file, returning empty dict if not exists or error."""
        if not file_path.exists():
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    return {}
                if YAML_AVAILABLE:
                    return yaml.safe_load(content) or {}
                else:
                    # Fallback: try to parse as JSON
                    import json
                    return json.loads(content) or {}
        except Exception as e:
            self._log_error(f"Failed to load {file_path.name}: {e}")
            return {}

    def _save_yaml(self, file_path: Path, data: Dict[str, Any]):
        """Save data to YAML file."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                if YAML_AVAILABLE:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                else:
                    import json
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

    # =========================================================================
    # WORK LOG MANAGEMENT
    # =========================================================================

    def log_work_entry(self,
                       operation: str,
                       description: str,
                       details: Optional[Dict] = None,
                       is_significant: bool = True):
        """
        Add entry to work_log.yaml.

        Called for significant operations:
        - File writes/edits
        - Task spawns
        - User prompts
        - Bash commands that modify things

        Keeps last MAX_WORK_LOG_ENTRIES entries.
        """
        if not is_significant:
            return

        log_data = self._load_yaml(WORK_LOG_FILE)
        entries = log_data.get('entries', [])

        # Redact secrets from description
        safe_description = redact_secrets(description[:200] if description else '')

        entry = {
            'timestamp': self._get_timestamp(),
            'local_time': self._get_local_time(),
            'operation': operation,
            'description': safe_description,
        }

        if details:
            # Include key details, truncating long values and redacting secrets
            safe_details = redact_dict(details)
            entry['details'] = {}
            for key, value in safe_details.items():
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

            # Log to work log
            if is_significant:
                self.log_work_entry(
                    operation=operation,
                    description=prompt,
                    details=details,
                    is_significant=True
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

            # Log to work log
            self.log_work_entry(
                operation='user_prompt',
                description=prompt[:200],
                details={'prompt_length': len(prompt)},
                is_significant=True
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

        Looks for GenSI commands, project references, etc.
        """
        prompt_lower = prompt.lower()

        # GenSI command detection
        if '/gensi' in prompt_lower:
            return 'GenSI Strategic Planning'
        if '/workshop' in prompt_lower:
            return 'Workshop Generation'

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


# ── pi-coding-agent bridge entry point ──────────────────────────────────────
# When called with --from-pi, reads JSON args from stdin and dispatches
# to the appropriate hook function.
# JSON schema: { "op": "on_tool_use"|"on_user_prompt"|"on_todo_write", ...fields }
def _run_from_pi():
    import sys as _sys
    import json as _json
    try:
        raw = _sys.stdin.read()
        args = _json.loads(raw) if raw.strip() else {}
    except Exception:
        _sys.exit(0)

    op = args.get("op", "")

    if op == "on_user_prompt":
        on_user_prompt(
            prompt=args.get("prompt", ""),
            session_id=args.get("session_id", ""),
            project=args.get("project", ""),
            cwd=args.get("cwd", ""),
        )
    elif op == "on_todo_write":
        on_todo_write(todos=args.get("todos", []))
    else:
        # Default: on_tool_use (op field may be "on_tool_use" or the operation name)
        on_tool_use(
            operation=args.get("operation", args.get("op", "unknown")),
            prompt=args.get("prompt", ""),
            details=args.get("details", {}),
            session_id=args.get("session_id", ""),
            project=args.get("project", ""),
            cwd=args.get("cwd", ""),
            subagent_context=args.get("subagent_context"),
        )


if __name__ == "__main__":
    import sys as _sys_entry
    if "--from-pi" in _sys_entry.argv:
        _run_from_pi()
        _sys_entry.exit(0)

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
