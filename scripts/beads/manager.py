"""
ABOUTME: Hybrid storage manager for Beads.
ABOUTME: Manages project-level and global beads databases.
"""
from pathlib import Path
from typing import Optional, List, Dict, Literal
from datetime import datetime, timezone
import json

from .storage import BeadsDatabase
from .models import Issue


class BeadsManager:
    """
    Manages hybrid storage: project-level + global beads.

    - Project-level (.beads/ in project root): Work items, tasks, molecules
    - Global (~/.claude/beads/): Cross-project concepts, shared patterns
    """

    def __init__(
        self,
        project_dir: Path,
        global_beads_dir: Path = None
    ):
        self.project_dir = Path(project_dir)
        self.global_beads_dir = global_beads_dir or (Path.home() / ".claude" / "beads")

        # Initialize databases
        self.project_db = BeadsDatabase(
            self.project_dir / ".beads",
            prefix="gz"
        )
        self.global_db = BeadsDatabase(
            self.global_beads_dir,
            prefix="gzg"  # gz-global
        )

        # Cross-database links stored in global
        self.links_file = self.global_beads_dir / "links.jsonl"
        self._links: List[Dict] = []
        self._links_loaded = False

    def _get_db(self, scope: str) -> BeadsDatabase:
        """Get database for scope."""
        return self.global_db if scope == "global" else self.project_db

    def _load_links(self) -> None:
        """Load cross-database links."""
        if self._links_loaded:
            return

        self._links = []
        self.global_beads_dir.mkdir(parents=True, exist_ok=True)
        if self.links_file.exists():
            with open(self.links_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        self._links.append(json.loads(line))
        self._links_loaded = True

    def _save_link(self, link: Dict) -> None:
        """Append link to links file."""
        self.global_beads_dir.mkdir(parents=True, exist_ok=True)
        with open(self.links_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(link) + '\n')
        self._links.append(link)

    def create(
        self,
        title: str,
        scope: Literal["project", "global"] = "project",
        **kwargs
    ) -> Issue:
        """Create issue in specified scope."""
        db = self._get_db(scope)
        return db.create(title=title, **kwargs)

    def get(self, issue_id: str) -> Optional[Issue]:
        """Get issue from any database."""
        # Try project first, then global
        issue = self.project_db.get(issue_id)
        if not issue:
            issue = self.global_db.get(issue_id)
        return issue

    def list(self, scope: str = "all", **kwargs) -> List[Issue]:
        """List issues from specified scope."""
        if scope == "project":
            return self.project_db.list(**kwargs)
        elif scope == "global":
            return self.global_db.list(**kwargs)
        else:
            # Combine both
            return self.project_db.list(**kwargs) + self.global_db.list(**kwargs)

    def link(
        self,
        source_id: str,
        target_id: str,
        link_type: str = "relates_to"
    ) -> bool:
        """Create cross-database link."""
        self._load_links()

        link = {
            "source": source_id,
            "target": target_id,
            "type": link_type,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self._save_link(link)
        return True

    def get_links(self, issue_id: str) -> List[Dict]:
        """Get all links for an issue."""
        self._load_links()
        return [l for l in self._links if l['source'] == issue_id or l['target'] == issue_id]

    def update(self, issue_id: str, **kwargs) -> Optional[Issue]:
        """Update issue in appropriate database."""
        issue = self.project_db.get(issue_id)
        if issue:
            return self.project_db.update(issue_id, **kwargs)
        issue = self.global_db.get(issue_id)
        if issue:
            return self.global_db.update(issue_id, **kwargs)
        return None

    def close(self, issue_id: str) -> Optional[Issue]:
        """Close issue in appropriate database."""
        issue = self.project_db.get(issue_id)
        if issue:
            return self.project_db.close(issue_id)
        issue = self.global_db.get(issue_id)
        if issue:
            return self.global_db.close(issue_id)
        return None

    def add_dependency(self, issue_id: str, depends_on_id: str) -> bool:
        """Add dependency (within same database)."""
        # Try project first
        if self.project_db.get(issue_id) and self.project_db.get(depends_on_id):
            return self.project_db.add_dependency(issue_id, depends_on_id)
        # Try global
        if self.global_db.get(issue_id) and self.global_db.get(depends_on_id):
            return self.global_db.add_dependency(issue_id, depends_on_id)
        # Cross-database dependency as link
        self.link(issue_id, depends_on_id, "depends_on")
        return True

    def ready(self, scope: str = "project") -> List[Issue]:
        """Get issues ready to work."""
        db = self._get_db(scope)
        return db.ready()
