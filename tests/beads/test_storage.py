"""Tests for Beads JSONL storage backend."""
import pytest
import tempfile
from pathlib import Path


class TestBeadsDatabase:
    @pytest.fixture
    def temp_beads_dir(self):
        """Create temporary .beads directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            beads_dir = Path(tmpdir) / ".beads"
            beads_dir.mkdir()
            yield beads_dir

    def test_create_issue(self, temp_beads_dir):
        """Database can create and retrieve issues."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        issue = db.create(title="Test task", type="task")

        assert issue.id.startswith("gz-")
        assert issue.title == "Test task"

        # Verify persisted
        retrieved = db.get(issue.id)
        assert retrieved.title == "Test task"

    def test_list_issues(self, temp_beads_dir):
        """Database can list issues with filters."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        db.create(title="Open task", type="task")
        db.create(title="Closed task", type="task")
        # Close the second task
        issues = db.list()
        db.close(issues[1].id)

        open_issues = db.list(status="open")
        assert len(open_issues) == 1
        assert open_issues[0].title == "Open task"

    def test_update_issue(self, temp_beads_dir):
        """Database can update issue fields."""
        from beads.storage import BeadsDatabase
        from beads.models import IssueStatus

        db = BeadsDatabase(temp_beads_dir)
        issue = db.create(title="Original")

        db.update(issue.id, title="Updated", status="in_progress")

        updated = db.get(issue.id)
        assert updated.title == "Updated"
        assert updated.status == IssueStatus.IN_PROGRESS

    def test_add_dependency(self, temp_beads_dir):
        """Database can add dependencies between issues."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        parent = db.create(title="Parent task")
        child = db.create(title="Child task")

        db.add_dependency(child.id, parent.id)

        child = db.get(child.id)
        parent = db.get(parent.id)
        assert parent.id in child.depends_on
        assert child.id in parent.blocks

    def test_issues_jsonl_format(self, temp_beads_dir):
        """Issues are stored in JSONL format."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        db.create(title="Test")

        jsonl_file = temp_beads_dir / "issues.jsonl"
        assert jsonl_file.exists()

        with open(jsonl_file, 'r') as f:
            lines = f.readlines()
        assert len(lines) == 1

    def test_ready_issues(self, temp_beads_dir):
        """Database can find issues ready to work (not blocked)."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        prereq = db.create(title="Prerequisite")
        blocked = db.create(title="Blocked task")
        unblocked = db.create(title="Unblocked task")

        db.add_dependency(blocked.id, prereq.id)

        ready = db.ready()
        ready_ids = [r.id for r in ready]

        assert prereq.id in ready_ids
        assert unblocked.id in ready_ids
        assert blocked.id not in ready_ids

    def test_ready_after_dependency_resolved(self, temp_beads_dir):
        """Blocked issue becomes ready after dependency is done."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        prereq = db.create(title="Prerequisite")
        blocked = db.create(title="Blocked task")

        db.add_dependency(blocked.id, prereq.id)

        # Initially blocked is not ready
        ready = db.ready()
        assert blocked.id not in [r.id for r in ready]

        # Complete the prerequisite
        db.update(prereq.id, status="done")

        # Now blocked should be ready
        ready = db.ready()
        assert blocked.id in [r.id for r in ready]

    def test_remove_dependency(self, temp_beads_dir):
        """Database can remove dependencies."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir)
        parent = db.create(title="Parent")
        child = db.create(title="Child")

        db.add_dependency(child.id, parent.id)
        db.remove_dependency(child.id, parent.id)

        child = db.get(child.id)
        parent = db.get(parent.id)
        assert parent.id not in child.depends_on
        assert child.id not in parent.blocks

    def test_custom_prefix(self, temp_beads_dir):
        """Database supports custom ID prefix."""
        from beads.storage import BeadsDatabase

        db = BeadsDatabase(temp_beads_dir, prefix="myproj")
        issue = db.create(title="Custom prefix")

        assert issue.id.startswith("myproj-")

    def test_persistence_across_instances(self, temp_beads_dir):
        """Issues persist across database instances."""
        from beads.storage import BeadsDatabase

        # Create issue with first instance
        db1 = BeadsDatabase(temp_beads_dir)
        issue = db1.create(title="Persistent")
        issue_id = issue.id

        # Load with new instance
        db2 = BeadsDatabase(temp_beads_dir)
        retrieved = db2.get(issue_id)

        assert retrieved is not None
        assert retrieved.title == "Persistent"
