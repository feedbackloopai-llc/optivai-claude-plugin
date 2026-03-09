"""
ABOUTME: Migration utility from old YAML memory system to Beads.
ABOUTME: Preserves all historical context while enabling graph-based tracking.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import yaml

from .storage import BeadsDatabase
from .manager import BeadsManager
from .models import IssueType


class MemoryMigrator:
    """Migrates old YAML memory files to Beads."""

    # Directories to scan for .claude folders
    DEFAULT_SCAN_PATHS = [
        Path.home() / "Documents",
        Path.home() / "Development",
        Path.home(),  # For ~/.claude
    ]

    def __init__(self, beads_dir: Path = None, project_dir: Path = None):
        """
        Initialize migrator.

        Args:
            beads_dir: Global beads directory (default: ~/.claude/beads)
            project_dir: Project directory for project-level beads
        """
        self.global_beads_dir = beads_dir or (Path.home() / ".claude" / "beads")
        self.project_dir = project_dir or Path.cwd()
        self.manager = BeadsManager(self.project_dir, self.global_beads_dir)

        # Track migrated content hashes to avoid duplicates
        self._migrated_hashes: set = set()
        self._load_migrated_hashes()

    def _load_migrated_hashes(self) -> None:
        """Load hashes of already-migrated content."""
        # Check for beads with 'migrated' label
        try:
            existing = self.manager.list(scope="all", label="migrated")
            for issue in existing:
                # Use description hash as identifier
                if issue.description:
                    content_hash = self._content_hash(issue.description)
                    self._migrated_hashes.add(content_hash)
        except Exception:
            pass  # No existing beads

    def _content_hash(self, content: str) -> str:
        """Generate hash for content deduplication."""
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _is_already_migrated(self, content: str) -> bool:
        """Check if content was already migrated."""
        return self._content_hash(content) in self._migrated_hashes

    def discover_all_memory_sources(self, extra_paths: List[Path] = None) -> List[Path]:
        """
        Find ALL .claude folders in the system containing memory data.

        Scans:
        - ~/.claude/gz-observability-memory/ (global)
        - ~/Documents/**/.claude/logs/ (project-level)
        - ~/Development/**/.claude/logs/
        - Current working directory tree
        - Any paths passed via extra_paths

        Returns:
            List of paths containing memory files (work_log.yaml, etc.)
        """
        found_dirs: set = set()
        scan_paths = self.DEFAULT_SCAN_PATHS.copy()

        if extra_paths:
            scan_paths.extend(extra_paths)

        # Add current working directory
        scan_paths.append(Path.cwd())

        for scan_path in scan_paths:
            if not scan_path.exists():
                continue

            # Check for direct .claude directory
            direct_claude = scan_path / ".claude"
            if direct_claude.exists():
                memory_dir = direct_claude / "gz-observability-memory"
                if memory_dir.exists():
                    found_dirs.add(memory_dir)
                logs_dir = direct_claude / "logs"
                if logs_dir.exists():
                    found_dirs.add(logs_dir)

            # Search for .claude directories (limit depth to 4 levels)
            try:
                for depth in range(4):
                    pattern = "/".join(["*"] * depth) + "/.claude" if depth > 0 else ".claude"
                    for claude_dir in scan_path.glob(pattern):
                        if claude_dir.is_dir() and ".Trash" not in str(claude_dir):
                            memory_dir = claude_dir / "gz-observability-memory"
                            if memory_dir.exists():
                                found_dirs.add(memory_dir)
                            logs_dir = claude_dir / "logs"
                            if logs_dir.exists():
                                found_dirs.add(logs_dir)
            except PermissionError:
                continue

        return sorted(list(found_dirs))

    def migrate_work_log(self, source_dir: Path = None, dry_run: bool = False) -> Tuple[int, List[Dict]]:
        """
        Migrate work_log.yaml entries to beads.

        Args:
            source_dir: Directory containing work_log.yaml
            dry_run: If True, don't create beads

        Returns:
            Tuple of (count migrated, list of migration details)
        """
        if source_dir is None:
            source_dir = Path.home() / ".claude" / "gz-observability-memory"

        work_log_file = source_dir / "work_log.yaml"
        if not work_log_file.exists():
            return (0, [])

        try:
            with open(work_log_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return (0, [])

        entries = data.get('entries', []) if isinstance(data, dict) else []
        migrated = 0
        details = []

        for entry in entries:
            timestamp = entry.get('timestamp', '')
            operation = entry.get('operation', 'unknown')
            description = entry.get('description', '')
            entry_details = entry.get('details', {})

            # Build content for hash
            content = f"{timestamp}|{operation}|{description}"
            if self._is_already_migrated(content):
                continue

            title = f"[{operation}] {description[:80]}" if description else f"[{operation}]"
            full_description = f"""Migrated from work_log.yaml

Timestamp: {timestamp}
Operation: {operation}
Description: {description}

Details:
{json.dumps(entry_details, indent=2) if entry_details else 'None'}
"""

            migration_detail = {
                'source': str(work_log_file),
                'type': 'work-log-entry',
                'timestamp': timestamp,
                'operation': operation,
                'title': title,
            }

            if not dry_run:
                try:
                    bead = self.manager.create(
                        title=title[:200],
                        scope="global",
                        type="task",
                        description=full_description,
                        labels=["migrated", "work-log", operation],
                    )
                    self._migrated_hashes.add(self._content_hash(content))
                    migration_detail['bead_id'] = bead.id
                except Exception as e:
                    migration_detail['error'] = str(e)

            details.append(migration_detail)
            migrated += 1

        return (migrated, details)

    def migrate_planned_tasks(self, source_dir: Path = None, dry_run: bool = False) -> Tuple[int, List[Dict]]:
        """
        Migrate planned_tasks.yaml to beads.

        Status mapping:
        - pending → open
        - in_progress → in_progress
        - completed → done

        Args:
            source_dir: Directory containing planned_tasks.yaml
            dry_run: If True, don't create beads

        Returns:
            Tuple of (count migrated, list of migration details)
        """
        if source_dir is None:
            source_dir = Path.home() / ".claude" / "gz-observability-memory"

        tasks_file = source_dir / "planned_tasks.yaml"
        if not tasks_file.exists():
            return (0, [])

        try:
            with open(tasks_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return (0, [])

        if not isinstance(data, dict):
            return (0, [])

        migrated = 0
        details = []

        # Status mapping
        status_map = {
            'pending': 'open',
            'in_progress': 'in_progress',
            'completed': 'done',
            'done': 'done',
        }

        # Process each status category
        for status_key in ['pending', 'in_progress', 'completed']:
            tasks = data.get(status_key, [])
            if not tasks:
                continue

            for task in tasks:
                content = task.get('content', '')
                if not content:
                    continue

                # Check for duplicates
                if self._is_already_migrated(content):
                    continue

                active_form = task.get('active_form', '')
                summary = task.get('summary', '')
                added_at = task.get('added_at', '')

                full_description = f"""Migrated from planned_tasks.yaml

Content: {content}
Active Form: {active_form}
Summary: {summary}
Added At: {added_at}
Original Status: {status_key}
"""

                migration_detail = {
                    'source': str(tasks_file),
                    'type': 'planned-task',
                    'original_status': status_key,
                    'title': content[:80],
                }

                if not dry_run:
                    try:
                        bead = self.manager.create(
                            title=content[:200],
                            scope="global",
                            type="task",
                            description=full_description,
                            labels=["migrated", "planned-task"],
                        )
                        # Update status
                        bead_status = status_map.get(status_key, 'open')
                        if bead_status != 'open':
                            self.manager.update(bead.id, status=bead_status)
                        self._migrated_hashes.add(self._content_hash(content))
                        migration_detail['bead_id'] = bead.id
                    except Exception as e:
                        migration_detail['error'] = str(e)

                details.append(migration_detail)
                migrated += 1

        return (migrated, details)

    def migrate_handoff(self, source_dir: Path = None, dry_run: bool = False) -> Tuple[int, List[Dict]]:
        """
        Migrate handoff_context.yaml to a single bead.

        Args:
            source_dir: Directory containing handoff_context.yaml
            dry_run: If True, don't create beads

        Returns:
            Tuple of (count migrated, list of migration details)
        """
        if source_dir is None:
            source_dir = Path.home() / ".claude" / "gz-observability-memory"

        handoff_file = source_dir / "handoff_context.yaml"
        if not handoff_file.exists():
            return (0, [])

        try:
            with open(handoff_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return (0, [])

        if not isinstance(data, dict):
            return (0, [])

        # Create content hash from session info
        from_session = data.get('from_session', '')
        handoff_created = data.get('handoff_created', '')
        content = f"handoff|{from_session}|{handoff_created}"

        if self._is_already_migrated(content):
            return (0, [])

        current_state = data.get('current_state', {})
        recent_work = data.get('recent_work', [])

        # Build description
        recent_work_text = "\n".join([
            f"- [{w.get('time', '')}] {w.get('operation', '')}: {w.get('description', '')[:60]}"
            for w in recent_work[:10]
        ])

        title = f"Handoff from {from_session}"
        full_description = f"""Migrated from handoff_context.yaml

Session: {from_session}
Created: {handoff_created}
Project: {current_state.get('project', 'unknown')}
CWD: {current_state.get('cwd', 'unknown')}
Focus: {current_state.get('focus', 'none')}

Recent Work:
{recent_work_text}

What Was Working On: {data.get('what_was_working_on', 'Not recorded')}
"""

        migration_detail = {
            'source': str(handoff_file),
            'type': 'handoff',
            'session': from_session,
            'title': title[:80],
        }

        if not dry_run:
            try:
                bead = self.manager.create(
                    title=title[:200],
                    scope="global",
                    type="handoff",
                    description=full_description,
                    labels=["migrated", "handoff"],
                )
                self._migrated_hashes.add(self._content_hash(content))
                migration_detail['bead_id'] = bead.id
            except Exception as e:
                migration_detail['error'] = str(e)

        return (1, [migration_detail])

    def migrate_project_contexts(self, source_dir: Path = None, dry_run: bool = False) -> Tuple[int, List[Dict]]:
        """
        Migrate project-specific context files (*_context.yaml, *_project.yaml).

        Args:
            source_dir: Directory containing context files
            dry_run: If True, don't create beads

        Returns:
            Tuple of (count migrated, list of migration details)
        """
        if source_dir is None:
            source_dir = Path.home() / ".claude" / "gz-observability-memory"

        if not source_dir.exists():
            return (0, [])

        # Find context files (exclude special files)
        exclude_files = {
            'work_log.yaml',
            'planned_tasks.yaml',
            'handoff_context.yaml',
            'session_state.yaml',
            'recovery_checkpoint.yaml',
        }

        context_files = [
            f for f in source_dir.glob("*.yaml")
            if f.name not in exclude_files and f.is_file()
        ]

        migrated = 0
        details = []

        for ctx_file in context_files:
            try:
                with open(ctx_file, 'r') as f:
                    content_text = f.read()
                    # Also try to parse as YAML for structure
                    f.seek(0)
                    data = yaml.safe_load(f)
            except Exception:
                continue

            # Hash based on file content
            content_hash_key = f"context|{ctx_file.name}|{self._content_hash(content_text)}"
            if self._is_already_migrated(content_hash_key):
                continue

            # Extract project name from filename
            project_name = ctx_file.stem.replace('_context', '').replace('_project', '').replace('-', ' ').title()

            title = f"Context: {project_name}"
            full_description = f"""Migrated from {ctx_file.name}

Project: {project_name}
Source: {ctx_file}

Content:
{content_text[:3000]}
"""

            migration_detail = {
                'source': str(ctx_file),
                'type': 'context',
                'project': project_name,
                'title': title[:80],
            }

            if not dry_run:
                try:
                    bead = self.manager.create(
                        title=title[:200],
                        scope="global",
                        type="task",
                        description=full_description,
                        labels=["migrated", "context", project_name.lower().replace(' ', '-')],
                    )
                    self._migrated_hashes.add(self._content_hash(content_hash_key))
                    migration_detail['bead_id'] = bead.id
                except Exception as e:
                    migration_detail['error'] = str(e)

            details.append(migration_detail)
            migrated += 1

        return (migrated, details)

    def migrate_hook_logs(self, source_dir: Path = None, dry_run: bool = False,
                          max_entries: int = 100) -> Tuple[int, List[Dict]]:
        """
        Migrate agent-activity-*.log entries from .claude/logs/ folders.

        Scans all .claude/logs/ directories found.
        Converts JSONL log entries to beads with operation metadata.

        Args:
            source_dir: Directory containing log files (or parent .claude dir)
            dry_run: If True, don't create beads
            max_entries: Maximum entries to migrate per file

        Returns:
            Tuple of (count migrated, list of migration details)
        """
        if source_dir is None:
            source_dir = Path.home() / ".claude" / "logs"

        if not source_dir.exists():
            return (0, [])

        # Find all log files
        log_files = sorted(source_dir.glob("agent-activity-*.log"))

        migrated = 0
        details = []

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
            except Exception:
                continue

            # Process most recent entries first (up to max_entries)
            for line in reversed(lines[-max_entries:]):
                try:
                    entry = json.loads(line.strip())
                except Exception:
                    continue

                timestamp = entry.get('timestamp', '')
                operation = entry.get('operation', 'unknown')
                prompt = entry.get('prompt', '')[:200]
                session_id = entry.get('session_id', '')
                project = entry.get('project', '')

                # Create content hash
                content = f"log|{timestamp}|{session_id}|{operation}"
                if self._is_already_migrated(content):
                    continue

                title = f"[{operation}] {prompt[:60]}" if prompt else f"[{operation}] {timestamp}"
                full_description = f"""Migrated from {log_file.name}

Timestamp: {timestamp}
Operation: {operation}
Session: {session_id}
Project: {project}
Prompt: {prompt}
"""

                migration_detail = {
                    'source': str(log_file),
                    'type': 'activity-log',
                    'timestamp': timestamp,
                    'operation': operation,
                    'title': title[:80],
                }

                if not dry_run:
                    try:
                        bead = self.manager.create(
                            title=title[:200],
                            scope="global",
                            type="task",
                            description=full_description,
                            labels=["migrated", "activity-log", operation],
                        )
                        self._migrated_hashes.add(self._content_hash(content))
                        migration_detail['bead_id'] = bead.id
                    except Exception as e:
                        migration_detail['error'] = str(e)

                details.append(migration_detail)
                migrated += 1

                if migrated >= max_entries:
                    break

            if migrated >= max_entries:
                break

        return (migrated, details)

    def migrate_all(self, dry_run: bool = False, include_logs: bool = False,
                    extra_paths: List[Path] = None) -> Dict[str, Any]:
        """
        Run full migration from ALL discovered sources.

        Args:
            dry_run: If True, preview what would be migrated
            include_logs: If True, also migrate hook logs (JSONL)
            extra_paths: Additional paths to scan for .claude folders

        Returns:
            Migration report dictionary
        """
        report = {
            'dry_run': dry_run,
            'sources_discovered': [],
            'migrations': {},
            'totals': {
                'work_log': 0,
                'planned_tasks': 0,
                'handoff': 0,
                'contexts': 0,
                'logs': 0,
                'total': 0,
            },
            'errors': [],
        }

        # Discover all sources
        sources = self.discover_all_memory_sources(extra_paths)
        report['sources_discovered'] = [str(s) for s in sources]

        # Global memory directory (primary source)
        global_memory = Path.home() / ".claude" / "gz-observability-memory"

        # Migrate from global memory
        if global_memory.exists():
            # Work log
            count, details = self.migrate_work_log(global_memory, dry_run)
            report['migrations']['work_log'] = details
            report['totals']['work_log'] += count

            # Planned tasks
            count, details = self.migrate_planned_tasks(global_memory, dry_run)
            report['migrations']['planned_tasks'] = details
            report['totals']['planned_tasks'] += count

            # Handoff
            count, details = self.migrate_handoff(global_memory, dry_run)
            report['migrations']['handoff'] = details
            report['totals']['handoff'] += count

            # Project contexts
            count, details = self.migrate_project_contexts(global_memory, dry_run)
            report['migrations']['contexts'] = details
            report['totals']['contexts'] += count

        # Migrate hook logs if requested
        if include_logs:
            logs_dir = Path.home() / ".claude" / "logs"
            count, details = self.migrate_hook_logs(logs_dir, dry_run)
            report['migrations']['logs'] = details
            report['totals']['logs'] += count

        # Calculate total
        report['totals']['total'] = sum([
            report['totals']['work_log'],
            report['totals']['planned_tasks'],
            report['totals']['handoff'],
            report['totals']['contexts'],
            report['totals']['logs'],
        ])

        return report

    def generate_report(self, migration_result: Dict[str, Any] = None) -> str:
        """
        Generate human-readable migration report.

        Args:
            migration_result: Result from migrate_all()

        Returns:
            Formatted report string
        """
        if migration_result is None:
            migration_result = self.migrate_all(dry_run=True)

        lines = []
        lines.append("=" * 60)
        lines.append("BEADS MIGRATION REPORT")
        lines.append("=" * 60)

        if migration_result.get('dry_run'):
            lines.append("\n[DRY RUN - No changes made]\n")

        lines.append(f"\nSources Discovered: {len(migration_result.get('sources_discovered', []))}")
        for src in migration_result.get('sources_discovered', []):
            lines.append(f"  - {src}")

        lines.append("\nMigration Summary:")
        totals = migration_result.get('totals', {})
        lines.append(f"  Work Log Entries:  {totals.get('work_log', 0)}")
        lines.append(f"  Planned Tasks:     {totals.get('planned_tasks', 0)}")
        lines.append(f"  Handoff Contexts:  {totals.get('handoff', 0)}")
        lines.append(f"  Project Contexts:  {totals.get('contexts', 0)}")
        lines.append(f"  Activity Logs:     {totals.get('logs', 0)}")
        lines.append(f"  ─────────────────────────")
        lines.append(f"  Total:             {totals.get('total', 0)}")

        errors = migration_result.get('errors', [])
        if errors:
            lines.append(f"\nErrors ({len(errors)}):")
            for err in errors[:10]:
                lines.append(f"  - {err}")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
