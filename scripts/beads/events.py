"""
ABOUTME: Event emission for Beads PostgreSQL integration.
ABOUTME: Writes bead events to log files for sync to The Well.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Issue


class BeadsEventEmitter:
    """
    Emits bead events to log files for PostgreSQL sync.

    Events are written to JSONL files in the same format as
    Claude Code activity logs, enabling reuse of existing sync infrastructure.
    """

    EVENT_TYPES = {
        'create': 'BEAD_CREATED',
        'update': 'BEAD_UPDATED',
        'close': 'BEAD_CLOSED',
        'dependency_add': 'DEPENDENCY_ADDED',
        'dependency_remove': 'DEPENDENCY_REMOVED',
        'molecule_instantiate': 'MOLECULE_INSTANTIATED',
    }

    def __init__(self, log_dir: Path):
        """
        Initialize event emitter.

        Args:
            log_dir: Directory for event log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # In-memory buffer for testing
        self._pending: List[Dict] = []

    def _get_log_file(self) -> Path:
        """Get today's log file."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        return self.log_dir / f"beads-events-{date_str}.log"

    def emit(
        self,
        event_type: str,
        bead_id: str,
        metadata: Dict[str, Any],
        actor_id: str = None
    ) -> None:
        """
        Emit a bead event.

        Args:
            event_type: Type of event (BEAD_CREATED, etc.)
            bead_id: ID of the affected bead
            metadata: Event-specific metadata
            actor_id: Who triggered the event
        """
        now = datetime.now(timezone.utc)

        event = {
            'timestamp': now.isoformat(),
            'date': now.strftime('%Y-%m-%d'),
            'time': now.strftime('%H:%M:%S'),
            'event_type': event_type,
            'bead_id': bead_id,
            'actor_id': actor_id or os.environ.get('CLAUDE_AGENT_ID', 'unknown'),
            'session_id': os.environ.get('CLAUDE_CODE_SESSION_ID', ''),
            'metadata': metadata,
            # Fields for PostgreSQL sync compatibility
            'source_system': 'CLAUDE_CODE',
            'tenant_id': 'CLAUDE_CODE',
            'subject_type': 'BEAD',
        }

        # Write to log file
        with open(self._get_log_file(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')

        # Buffer for testing
        self._pending.append(event)

    def emit_bead_created(self, issue: 'Issue') -> None:
        """Emit BEAD_CREATED event."""
        self.emit(
            event_type='BEAD_CREATED',
            bead_id=issue.id,
            metadata={
                'bead_id': issue.id,
                'bead_type': issue.type.value,
                'title': issue.title,
                'priority': issue.priority,
                'parent': issue.parent,
                'labels': issue.labels,
                'created_by': issue.created_by,
            }
        )

    def emit_bead_updated(self, issue: 'Issue', changes: Dict[str, Any]) -> None:
        """Emit BEAD_UPDATED event."""
        self.emit(
            event_type='BEAD_UPDATED',
            bead_id=issue.id,
            metadata={
                'bead_id': issue.id,
                'changes': changes,
            }
        )

    def emit_bead_closed(self, bead_id: str, reason: str = "") -> None:
        """Emit BEAD_CLOSED event."""
        self.emit(
            event_type='BEAD_CLOSED',
            bead_id=bead_id,
            metadata={
                'bead_id': bead_id,
                'reason': reason,
            }
        )

    def emit_dependency_added(self, source_id: str, target_id: str) -> None:
        """Emit DEPENDENCY_ADDED event."""
        self.emit(
            event_type='DEPENDENCY_ADDED',
            bead_id=source_id,
            metadata={
                'source_bead_id': source_id,
                'target_bead_id': target_id,
                'edge_type': 'DEPENDS_ON',
            }
        )

    def emit_dependency_removed(self, source_id: str, target_id: str) -> None:
        """Emit DEPENDENCY_REMOVED event."""
        self.emit(
            event_type='DEPENDENCY_REMOVED',
            bead_id=source_id,
            metadata={
                'source_bead_id': source_id,
                'target_bead_id': target_id,
                'edge_type': 'DEPENDS_ON',
            }
        )

    def emit_molecule_instantiated(
        self,
        molecule_id: str,
        parent_id: str,
        child_ids: List[str]
    ) -> None:
        """Emit MOLECULE_INSTANTIATED event."""
        self.emit(
            event_type='MOLECULE_INSTANTIATED',
            bead_id=molecule_id,
            metadata={
                'molecule_id': molecule_id,
                'parent_id': parent_id,
                'child_ids': child_ids,
                'step_count': len(child_ids),
            }
        )

    def get_pending_events(self) -> List[Dict]:
        """Get pending events (for testing)."""
        return self._pending

    def clear_pending(self) -> None:
        """Clear pending events buffer."""
        self._pending = []
