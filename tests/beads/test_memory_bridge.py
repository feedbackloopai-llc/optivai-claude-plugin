"""Tests for memory bridge integration."""
import pytest
import tempfile
from pathlib import Path


class TestMemoryBeadsBridge:
    @pytest.fixture
    def temp_dirs(self):
        """Create temp project and global dirs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "my-project"
            project_dir.mkdir()
            global_dir = Path(tmpdir) / "global-beads"
            global_dir.mkdir()
            yield project_dir, global_dir

    def test_sync_from_todo_creates_beads(self, temp_dirs):
        """Syncing todos creates beads."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        todos = [
            {'id': '1', 'content': 'Task one', 'status': 'pending'},
            {'id': '2', 'content': 'Task two', 'status': 'in_progress'},
        ]

        synced = bridge.sync_from_todo(todos)

        assert synced == 2

        # Verify beads were created
        beads = bridge.manager.list(scope="project")
        assert len(beads) == 2

    def test_sync_from_todo_updates_existing(self, temp_dirs):
        """Syncing todos updates existing beads."""
        from beads.memory_bridge import MemoryBeadsBridge
        from beads.models import IssueStatus

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        # First sync
        todos = [{'id': '1', 'content': 'Task one', 'status': 'pending'}]
        bridge.sync_from_todo(todos)

        # Second sync with status change
        todos = [{'id': '1', 'content': 'Task one', 'status': 'completed'}]
        synced = bridge.sync_from_todo(todos)

        assert synced == 1

        # Verify status was updated
        beads = bridge.manager.list(scope="project")
        assert beads[0].status == IssueStatus.DONE

    def test_sync_to_todo_exports_beads(self, temp_dirs):
        """Exporting beads to todo format."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        # Create some beads
        bridge.manager.create(title="Bead task 1", scope="project", type="task")
        bridge.manager.create(title="Bead task 2", scope="project", type="task")

        todos = bridge.sync_to_todo()

        assert len(todos) == 2
        assert todos[0]['content'] == 'Bead task 1'
        assert todos[0]['status'] == 'pending'

    def test_get_ready_beads(self, temp_dirs):
        """Getting ready beads for propulsion."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        # Create ready bead
        bridge.manager.create(title="Ready task", scope="project")

        ready = bridge.get_ready_beads()

        assert len(ready) == 1
        assert ready[0].title == "Ready task"

    def test_get_ready_summary(self, temp_dirs):
        """Getting human-readable ready summary."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        bridge.manager.create(title="Task A", scope="project")
        bridge.manager.create(title="Task B", scope="project")

        summary = bridge.get_ready_summary()

        assert "2 bead(s) ready" in summary
        assert "Task A" in summary
        assert "Task B" in summary

    def test_empty_ready_summary(self, temp_dirs):
        """Empty summary when no beads ready."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        summary = bridge.get_ready_summary()

        assert "No beads ready" in summary

    def test_skip_molecules_in_todo_export(self, temp_dirs):
        """Molecules are not exported as todos."""
        from beads.memory_bridge import MemoryBeadsBridge

        project_dir, global_dir = temp_dirs
        bridge = MemoryBeadsBridge(project_dir, global_dir)

        # Create task and molecule
        bridge.manager.create(title="Regular task", scope="project", type="task")
        bridge.manager.create(title="Workflow template", scope="project", type="molecule")

        todos = bridge.sync_to_todo()

        assert len(todos) == 1
        assert todos[0]['content'] == 'Regular task'
