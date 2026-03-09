"""
ABOUTME: JSONL storage backend for Beads.
ABOUTME: Provides file-based persistence with git-friendly format.
"""
import json
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from filelock import FileLock

from .models import Issue, IssueStatus, IssueType


class BeadsDatabase:
    """
    JSONL-based storage for beads issues.

    Storage layout:
        .beads/
        ├── issues.jsonl      # All issues (append-only, compacted periodically)
        ├── config.yaml       # Database configuration
        └── .gitignore        # Excludes temp files
    """

    def __init__(self, beads_dir: Path, prefix: str = "gz", event_emitter=None):
        """
        Initialize beads database.

        Args:
            beads_dir: Path to .beads directory
            prefix: ID prefix for new issues (default: "gz")
            event_emitter: Optional event emitter for PostgreSQL sync
        """
        self.beads_dir = Path(beads_dir)
        self.prefix = prefix
        self.issues_file = self.beads_dir / "issues.jsonl"
        self.lock_file = self.beads_dir / ".lock"
        self.event_emitter = event_emitter

        # Ensure directory exists
        self.beads_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache (loaded from JSONL on first access)
        self._cache: Dict[str, Issue] = {}
        self._cache_loaded = False

    def _generate_id(self) -> str:
        """Generate unique bead ID in format: prefix-xxxxx."""
        chars = string.ascii_lowercase + string.digits
        suffix = ''.join(secrets.choice(chars) for _ in range(5))
        return f"{self.prefix}-{suffix}"

    def _load_cache(self) -> None:
        """Load all issues from JSONL into memory cache."""
        if self._cache_loaded:
            return

        self._cache = {}
        if self.issues_file.exists():
            with open(self.issues_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        issue = Issue.from_dict(data)
                        self._cache[issue.id] = issue
        self._cache_loaded = True

    def _save_issue(self, issue: Issue) -> None:
        """Append issue to JSONL file."""
        if issue.ephemeral:
            return  # Wisps not persisted

        with FileLock(str(self.lock_file)):
            with open(self.issues_file, 'a', encoding='utf-8') as f:
                f.write(issue.to_jsonl() + '\n')

    def _rewrite_jsonl(self) -> None:
        """Rewrite JSONL with current cache state (compaction)."""
        with FileLock(str(self.lock_file)):
            with open(self.issues_file, 'w', encoding='utf-8') as f:
                for issue in self._cache.values():
                    if not issue.ephemeral:
                        f.write(issue.to_jsonl() + '\n')

    def create(
        self,
        title: str,
        type: str = "task",
        description: str = "",
        priority: int = 2,
        parent: str = "",
        labels: List[str] = None,
        ephemeral: bool = False,
        created_by: str = ""
    ) -> Issue:
        """Create a new issue."""
        self._load_cache()

        issue = Issue(
            id=self._generate_id(),
            title=title,
            type=IssueType(type),
            description=description,
            priority=priority,
            parent=parent,
            labels=labels or [],
            ephemeral=ephemeral,
            created_by=created_by,
            created_at=datetime.now(timezone.utc).isoformat()
        )

        self._cache[issue.id] = issue
        self._save_issue(issue)

        # Update parent's children list
        if parent and parent in self._cache:
            self._cache[parent].children.append(issue.id)
            self._rewrite_jsonl()

        # Emit event if emitter configured
        if self.event_emitter:
            self.event_emitter.emit_bead_created(issue)

        return issue

    def get(self, issue_id: str) -> Optional[Issue]:
        """Get issue by ID."""
        self._load_cache()
        return self._cache.get(issue_id)

    def list(
        self,
        status: str = None,
        type: str = None,
        parent: str = None,
        label: str = None,
        assignee: str = None
    ) -> List[Issue]:
        """List issues with optional filters."""
        self._load_cache()

        results = []
        for issue in self._cache.values():
            if status and issue.status.value != status:
                continue
            if type and issue.type.value != type:
                continue
            if parent is not None and issue.parent != parent:
                continue
            if label and label not in issue.labels:
                continue
            if assignee is not None:
                if assignee == "" and issue.assignee != "":
                    continue
                elif assignee != "" and issue.assignee != assignee:
                    continue
            results.append(issue)

        return sorted(results, key=lambda i: (i.priority, i.created_at))

    def update(self, issue_id: str, **kwargs) -> Optional[Issue]:
        """Update issue fields."""
        self._load_cache()

        issue = self._cache.get(issue_id)
        if not issue:
            return None

        old_values = {}
        for key, value in kwargs.items():
            if key == 'status' and isinstance(value, str):
                value = IssueStatus(value)
            if key == 'type' and isinstance(value, str):
                value = IssueType(value)
            if hasattr(issue, key):
                old_values[key] = getattr(issue, key)
                setattr(issue, key, value)

        issue.updated_at = datetime.now(timezone.utc).isoformat()
        self._rewrite_jsonl()

        # Emit event if emitter configured
        if self.event_emitter:
            self.event_emitter.emit_bead_updated(issue, kwargs)

        return issue

    def close(self, issue_id: str, reason: str = "") -> Optional[Issue]:
        """Close an issue."""
        return self.update(
            issue_id,
            status=IssueStatus.CLOSED,
            closed_at=datetime.now(timezone.utc).isoformat()
        )

    def add_dependency(self, issue_id: str, depends_on_id: str) -> bool:
        """Add dependency: issue_id depends on depends_on_id."""
        self._load_cache()

        issue = self._cache.get(issue_id)
        dep = self._cache.get(depends_on_id)

        if not issue or not dep:
            return False

        if depends_on_id not in issue.depends_on:
            issue.depends_on.append(depends_on_id)
        if issue_id not in dep.blocks:
            dep.blocks.append(issue_id)

        self._rewrite_jsonl()

        # Emit event if emitter configured
        if self.event_emitter:
            self.event_emitter.emit_dependency_added(issue_id, depends_on_id)

        return True

    def remove_dependency(self, issue_id: str, depends_on_id: str) -> bool:
        """Remove dependency relationship."""
        self._load_cache()

        issue = self._cache.get(issue_id)
        dep = self._cache.get(depends_on_id)

        if issue and depends_on_id in issue.depends_on:
            issue.depends_on.remove(depends_on_id)
        if dep and issue_id in dep.blocks:
            dep.blocks.remove(issue_id)

        self._rewrite_jsonl()
        return True

    def ready(self) -> List[Issue]:
        """Get issues that are ready to work (open, not blocked)."""
        self._load_cache()

        results = []
        for issue in self._cache.values():
            if issue.status != IssueStatus.OPEN:
                continue
            # Check if all dependencies are closed/done
            blocked = False
            for dep_id in issue.depends_on:
                dep = self._cache.get(dep_id)
                if dep and dep.status not in (IssueStatus.DONE, IssueStatus.CLOSED):
                    blocked = True
                    break
            if not blocked:
                results.append(issue)

        return sorted(results, key=lambda i: (i.priority, i.created_at))
