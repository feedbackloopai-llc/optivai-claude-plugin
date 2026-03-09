"""Tests for hybrid storage manager."""
import pytest
import tempfile
from pathlib import Path


class TestBeadsManager:
    @pytest.fixture
    def temp_dirs(self):
        """Create temp project and global dirs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "my-project"
            project_dir.mkdir()
            global_dir = Path(tmpdir) / "global-beads"
            global_dir.mkdir()
            yield project_dir, global_dir

    def test_project_level_storage(self, temp_dirs):
        """Manager uses project-level storage for work items."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        issue = manager.create(title="Project task", scope="project")
        assert (project_dir / ".beads" / "issues.jsonl").exists()

    def test_global_storage(self, temp_dirs):
        """Manager uses global storage for cross-project items."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        issue = manager.create(title="Global concept", scope="global")
        assert (global_dir / "issues.jsonl").exists()

    def test_cross_project_link(self, temp_dirs):
        """Can link project issue to global concept."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        global_issue = manager.create(title="API Design Pattern", scope="global")
        project_issue = manager.create(title="Implement API", scope="project")

        manager.link(project_issue.id, global_issue.id, "relates_to")

        links = manager.get_links(project_issue.id)
        assert len(links) == 1
        assert links[0]['target'] == global_issue.id

    def test_get_from_any_database(self, temp_dirs):
        """Manager can get issues from either database."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        global_issue = manager.create(title="Global", scope="global")
        project_issue = manager.create(title="Project", scope="project")

        # Should find both
        assert manager.get(global_issue.id) is not None
        assert manager.get(project_issue.id) is not None

    def test_list_all_scopes(self, temp_dirs):
        """Manager can list from all scopes."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        manager.create(title="Global 1", scope="global")
        manager.create(title="Project 1", scope="project")
        manager.create(title="Project 2", scope="project")

        all_issues = manager.list(scope="all")
        project_issues = manager.list(scope="project")
        global_issues = manager.list(scope="global")

        assert len(all_issues) == 3
        assert len(project_issues) == 2
        assert len(global_issues) == 1

    def test_different_prefixes(self, temp_dirs):
        """Project and global databases use different ID prefixes."""
        from beads.manager import BeadsManager

        project_dir, global_dir = temp_dirs
        manager = BeadsManager(project_dir, global_dir)

        global_issue = manager.create(title="Global", scope="global")
        project_issue = manager.create(title="Project", scope="project")

        assert project_issue.id.startswith("gz-")
        assert global_issue.id.startswith("gzg-")
