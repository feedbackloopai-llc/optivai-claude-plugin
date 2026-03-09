"""
ABOUTME: Core data models for Beads graph-based knowledge system.
ABOUTME: Implements Issue (bead), relationships, and serialization.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
import json


class IssueStatus(str, Enum):
    """Issue lifecycle states."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CLOSED = "closed"
    HOOKED = "hooked"      # Attached to agent's hook
    PINNED = "pinned"      # Permanent record (handoffs)


class IssueType(str, Enum):
    """Issue classification types."""
    TASK = "task"
    BUG = "bug"
    FEATURE = "feature"
    EPIC = "epic"
    MOLECULE = "molecule"  # Workflow template
    AGENT = "agent"        # Agent bead with hook slot
    HANDOFF = "handoff"    # Handoff bead for work assignment


@dataclass
class Issue:
    """
    A bead in the knowledge graph.

    Represents a work item with relationships to other beads.
    Follows Gastown's Issue structure with FeedbackLoopAI adaptations.
    """
    id: str
    title: str
    description: str = ""
    status: IssueStatus = IssueStatus.OPEN
    priority: int = 2  # 0=critical, 1=high, 2=medium, 3=low, 4=backlog
    type: IssueType = IssueType.TASK

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = ""
    updated_at: str = ""
    closed_at: str = ""

    # Graph relationships
    parent: str = ""                           # Parent issue ID
    children: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)   # Prerequisites
    blocks: List[str] = field(default_factory=list)       # What this blocks
    blocked_by: List[str] = field(default_factory=list)   # What blocks this

    # Metadata
    labels: List[str] = field(default_factory=list)
    assignee: str = ""

    # Agent slots (type=agent only)
    hook_bead: str = ""      # Currently attached work
    agent_state: str = ""    # spawning, working, done, stuck

    # Ephemeral flag (wisps - not persisted to JSONL)
    ephemeral: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSONL storage."""
        data = asdict(self)
        # Convert enums to strings
        data['status'] = self.status.value
        data['type'] = self.type.value
        # Remove empty lists/strings for compact storage
        return {k: v for k, v in data.items() if v or v == 0 or v is False}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Issue':
        """Deserialize from dictionary."""
        # Convert string enums back
        if 'status' in data:
            data['status'] = IssueStatus(data['status'])
        if 'type' in data:
            data['type'] = IssueType(data['type'])
        # Ensure list fields exist
        for list_field in ['children', 'depends_on', 'blocks', 'blocked_by', 'labels']:
            if list_field not in data:
                data[list_field] = []
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_jsonl(self) -> str:
        """Serialize to JSONL line."""
        return json.dumps(self.to_dict(), separators=(',', ':'))
