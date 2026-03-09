"""Tests for Beads data models."""
import pytest
from datetime import datetime, timezone


class TestIssue:
    def test_create_minimal_issue(self):
        """Issue can be created with just ID and title."""
        from beads.models import Issue, IssueStatus, IssueType

        issue = Issue(id="gz-abc12", title="Test issue")
        assert issue.id == "gz-abc12"
        assert issue.title == "Test issue"
        assert issue.status == IssueStatus.OPEN
        assert issue.type == IssueType.TASK
        assert issue.priority == 2  # Default medium

    def test_issue_with_dependencies(self):
        """Issue tracks dependency relationships."""
        from beads.models import Issue

        issue = Issue(
            id="gz-def34",
            title="Dependent task",
            depends_on=["gz-abc12"],
            blocks=["gz-ghi56"]
        )
        assert "gz-abc12" in issue.depends_on
        assert "gz-ghi56" in issue.blocks

    def test_issue_parent_child_hierarchy(self):
        """Issue supports parent-child hierarchy."""
        from beads.models import Issue

        child = Issue(id="gz-child1", title="Child", parent="gz-parent1")
        assert child.parent == "gz-parent1"

    def test_issue_to_dict_roundtrip(self):
        """Issue serializes to dict and back."""
        from beads.models import Issue

        original = Issue(
            id="gz-test1",
            title="Roundtrip test",
            description="Full description",
            depends_on=["gz-dep1"],
            labels=["urgent", "bug"]
        )
        data = original.to_dict()
        restored = Issue.from_dict(data)
        assert restored.id == original.id
        assert restored.depends_on == original.depends_on
        assert restored.labels == original.labels

    def test_issue_to_jsonl(self):
        """Issue serializes to JSONL format."""
        from beads.models import Issue
        import json

        issue = Issue(id="gz-json1", title="JSON test")
        jsonl = issue.to_jsonl()

        # Should be valid JSON
        data = json.loads(jsonl)
        assert data['id'] == "gz-json1"
        assert data['title'] == "JSON test"

    def test_issue_status_enum(self):
        """IssueStatus enum has correct values."""
        from beads.models import IssueStatus

        assert IssueStatus.OPEN.value == "open"
        assert IssueStatus.IN_PROGRESS.value == "in_progress"
        assert IssueStatus.DONE.value == "done"
        assert IssueStatus.CLOSED.value == "closed"
        assert IssueStatus.HOOKED.value == "hooked"
        assert IssueStatus.PINNED.value == "pinned"

    def test_issue_type_enum(self):
        """IssueType enum has correct values."""
        from beads.models import IssueType

        assert IssueType.TASK.value == "task"
        assert IssueType.BUG.value == "bug"
        assert IssueType.FEATURE.value == "feature"
        assert IssueType.EPIC.value == "epic"
        assert IssueType.MOLECULE.value == "molecule"
        assert IssueType.AGENT.value == "agent"
        assert IssueType.HANDOFF.value == "handoff"
