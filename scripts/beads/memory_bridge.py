"""
ABOUTME: Bridge between Beads and existing memory system.
ABOUTME: Enables gradual migration from YAML to graph-based storage.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

from .storage import BeadsDatabase
from .manager import BeadsManager
from .models import Issue, IssueStatus


class MemoryBeadsBridge:
    """
    Bridges existing memory system with beads graph.

    - Syncs planned_tasks.yaml ↔ beads issues
    - Preserves backward compatibility
    - Enables gradual adoption
    """

    def __init__(
        self,
        project_dir: Path = None,
        global_beads_dir: Path = None
    ):
        self.project_dir = project_dir or Path.cwd()
        self.global_beads_dir = global_beads_dir or (Path.home() / ".claude" / "beads")

        # Initialize beads manager
        self.manager = BeadsManager(self.project_dir, self.global_beads_dir)

    def sync_from_todo(self, todos: List[Dict[str, Any]]) -> int:
        """
        Sync TodoWrite todos to beads.

        Called by memory_writer.on_todo_write() to mirror
        todo state in beads graph.

        Returns:
            Number of beads created or updated
        """
        synced = 0

        for todo in todos:
            # Extract content - TodoWrite uses 'content' or 'subject'
            content = todo.get('content', todo.get('subject', ''))
            if not content:
                continue

            status = todo.get('status', 'pending')
            todo_id = todo.get('id', '')

            # Search for existing bead by title match
            existing = self._find_bead_by_title(content)

            if existing:
                # Update status if changed
                bead_status = self._map_todo_status(status)
                if existing.status.value != bead_status:
                    self.manager.project_db.update(
                        existing.id,
                        status=bead_status
                    )
                    synced += 1
            else:
                # Create new bead
                new_bead = self.manager.create(
                    title=content[:200],  # Truncate long titles
                    scope="project",
                    type="task",
                    labels=["from-todo"],
                    description=f"Synced from TodoWrite\ntodo_id: {todo_id}" if todo_id else "Synced from TodoWrite"
                )
                # Set status from todo
                bead_status = self._map_todo_status(status)
                if bead_status != 'open':
                    self.manager.project_db.update(new_bead.id, status=bead_status)
                synced += 1

        return synced

    def sync_to_todo(self) -> List[Dict[str, Any]]:
        """
        Export beads as TodoWrite-compatible format.

        Returns:
            List of todo dicts for TodoWrite
        """
        todos = []
        issues = self.manager.list(scope="project")

        for issue in issues:
            # Skip molecules and epics (not individual tasks)
            if issue.type.value in ('molecule', 'epic'):
                continue

            todo = {
                'id': issue.id,
                'content': issue.title,
                'status': self._map_bead_status(issue.status.value),
            }
            todos.append(todo)

        return todos

    def _find_bead_by_title(self, title: str) -> Optional[Issue]:
        """Find bead by title prefix match."""
        truncated = title[:200]
        issues = self.manager.list(scope="project")

        for issue in issues:
            if issue.title == truncated:
                return issue
            # Also check if bead was created from this todo
            if "from-todo" in issue.labels and issue.title == truncated:
                return issue

        return None

    def _map_todo_status(self, todo_status: str) -> str:
        """Map todo status to bead status."""
        mapping = {
            'pending': 'open',
            'in_progress': 'in_progress',
            'completed': 'done',
            'done': 'done',
        }
        return mapping.get(todo_status, 'open')

    def _map_bead_status(self, bead_status: str) -> str:
        """Map bead status to todo status."""
        mapping = {
            'open': 'pending',
            'in_progress': 'in_progress',
            'done': 'completed',
            'closed': 'completed',
            'hooked': 'in_progress',
            'pinned': 'completed',
        }
        return mapping.get(bead_status, 'pending')

    def import_from_work_log(self, memory_dir: Path = None) -> int:
        """
        Import work_log.yaml entries as beads.

        Uses the migrate module for actual migration, but provides
        a convenient bridge-level interface.

        Args:
            memory_dir: Path to gz-observability-memory directory

        Returns:
            Number of entries imported
        """
        from .migrate import MemoryMigrator

        migrator = MemoryMigrator(
            beads_dir=self.global_beads_dir,
            project_dir=self.project_dir
        )
        count, _ = migrator.migrate_work_log(memory_dir)
        return count

    def import_from_planned_tasks(self, memory_dir: Path = None) -> int:
        """
        Import planned_tasks.yaml as beads.

        Uses the migrate module for actual migration, but provides
        a convenient bridge-level interface.

        Args:
            memory_dir: Path to gz-observability-memory directory

        Returns:
            Number of tasks imported
        """
        from .migrate import MemoryMigrator

        migrator = MemoryMigrator(
            beads_dir=self.global_beads_dir,
            project_dir=self.project_dir
        )
        count, _ = migrator.migrate_planned_tasks(memory_dir)
        return count

    def export_work_log_format(self) -> List[Dict[str, Any]]:
        """
        Export beads back to work_log.yaml format (backward compatibility).

        Converts beads with 'work-log' label back to the legacy format.

        Returns:
            List of work log entries in YAML-compatible format
        """
        entries = []

        # Get all beads with work-log label
        issues = self.manager.list(scope="all", label="work-log")

        for issue in issues:
            # Parse timestamp from description if available
            timestamp = issue.created_at

            # Try to extract operation from labels
            operation = "unknown"
            for label in issue.labels:
                if label not in ('migrated', 'work-log'):
                    operation = label
                    break

            # Build entry in work_log format
            entry = {
                'timestamp': timestamp,
                'local_time': timestamp[:19].replace('T', ' ') if timestamp else '',
                'operation': operation,
                'description': issue.title.replace(f'[{operation}] ', ''),
                'details': {
                    'bead_id': issue.id,
                    'migrated': True,
                }
            }
            entries.append(entry)

        # Sort by timestamp (newest first)
        entries.sort(key=lambda e: e.get('timestamp', ''), reverse=True)
        return entries

    def get_ready_beads(self) -> List[Issue]:
        """Get beads ready for work (for Propulsion Principle)."""
        return self.manager.project_db.ready()

    def get_ready_summary(self) -> str:
        """Get human-readable summary of ready beads."""
        ready = self.get_ready_beads()

        if not ready:
            return "No beads ready to work."

        lines = [f"{len(ready)} bead(s) ready:"]
        for issue in ready[:5]:  # Show top 5
            lines.append(f"  - {issue.id}: {issue.title}")

        if len(ready) > 5:
            lines.append(f"  ... and {len(ready) - 5} more")

        return "\n".join(lines)


# Singleton instance
_bridge_instance: Optional[MemoryBeadsBridge] = None


def get_bridge() -> MemoryBeadsBridge:
    """Get or create singleton bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = MemoryBeadsBridge()
    return _bridge_instance


def sync_todos_to_beads(todos: List[Dict[str, Any]]) -> int:
    """Convenience function to sync todos to beads."""
    try:
        return get_bridge().sync_from_todo(todos)
    except Exception:
        # Fail silently if beads not initialized
        return 0


def get_ready_beads_summary() -> str:
    """Convenience function to get ready beads summary."""
    try:
        return get_bridge().get_ready_summary()
    except Exception:
        return ""


def import_work_log(memory_dir: Path = None) -> int:
    """Convenience function to import work log entries."""
    try:
        return get_bridge().import_from_work_log(memory_dir)
    except Exception:
        return 0


def import_planned_tasks(memory_dir: Path = None) -> int:
    """Convenience function to import planned tasks."""
    try:
        return get_bridge().import_from_planned_tasks(memory_dir)
    except Exception:
        return 0


def export_as_work_log() -> List[Dict[str, Any]]:
    """Convenience function to export beads as work log format."""
    try:
        return get_bridge().export_work_log_format()
    except Exception:
        return []
