"""Tests for Beads CLI."""
import pytest
from click.testing import CliRunner


class TestCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_init_command(self, runner, tmp_path):
        """CLI can initialize beads database."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ['init', '--prefix', 'test'])
            assert result.exit_code == 0
            assert 'Initialized' in result.output

    def test_create_issue(self, runner, tmp_path):
        """CLI can create issues."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ['init', '--prefix', 'test'])
            assert result.exit_code == 0

            result = runner.invoke(cli, ['create', 'My first task'])
            assert result.exit_code == 0
            assert 'test-' in result.output

    def test_list_issues(self, runner, tmp_path):
        """CLI can list issues."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            runner.invoke(cli, ['create', 'Task 1'])
            runner.invoke(cli, ['create', 'Task 2'])

            result = runner.invoke(cli, ['list'])
            assert 'Task 1' in result.output
            assert 'Task 2' in result.output

    def test_show_issue(self, runner, tmp_path):
        """CLI can show issue details."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            create_result = runner.invoke(cli, ['create', 'Detailed task'])
            # Extract issue ID from output (format: "Created: test-xxxxx")
            issue_id = create_result.output.strip().split()[-1]

            result = runner.invoke(cli, ['show', issue_id])
            assert result.exit_code == 0
            assert 'Detailed task' in result.output

    def test_update_issue(self, runner, tmp_path):
        """CLI can update issue fields."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            create_result = runner.invoke(cli, ['create', 'Original title'])
            issue_id = create_result.output.strip().split()[-1]

            result = runner.invoke(cli, ['update', issue_id, '--title', 'Updated title'])
            assert result.exit_code == 0

            show_result = runner.invoke(cli, ['show', issue_id])
            assert 'Updated title' in show_result.output

    def test_close_issue(self, runner, tmp_path):
        """CLI can close issues."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            create_result = runner.invoke(cli, ['create', 'Task to close'])
            issue_id = create_result.output.strip().split()[-1]

            result = runner.invoke(cli, ['close', issue_id])
            assert result.exit_code == 0
            assert 'Closed' in result.output

    def test_depend_command(self, runner, tmp_path):
        """CLI can add dependencies."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            parent_result = runner.invoke(cli, ['create', 'Parent task'])
            child_result = runner.invoke(cli, ['create', 'Child task'])
            parent_id = parent_result.output.strip().split()[-1]
            child_id = child_result.output.strip().split()[-1]

            result = runner.invoke(cli, ['depend', child_id, parent_id])
            assert result.exit_code == 0
            assert 'depends on' in result.output

    def test_ready_command(self, runner, tmp_path):
        """CLI can show ready issues."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            runner.invoke(cli, ['create', 'Ready task'])

            result = runner.invoke(cli, ['ready'])
            assert result.exit_code == 0
            assert 'Ready task' in result.output

    def test_list_with_status_filter(self, runner, tmp_path):
        """CLI can filter list by status."""
        from beads.cli import cli

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            create_result = runner.invoke(cli, ['create', 'Open task'])
            issue_id = create_result.output.strip().split()[-1]
            runner.invoke(cli, ['close', issue_id])
            runner.invoke(cli, ['create', 'Another open task'])

            result = runner.invoke(cli, ['list', '--status', 'open'])
            assert 'Another open task' in result.output
            assert 'Open task' not in result.output  # Closed, so filtered out

    def test_list_json_output(self, runner, tmp_path):
        """CLI can output list as JSON."""
        from beads.cli import cli
        import json

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            runner.invoke(cli, ['create', 'JSON task'])

            result = runner.invoke(cli, ['list', '--json'])
            assert result.exit_code == 0
            # Should be valid JSON
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1

    def test_show_json_output(self, runner, tmp_path):
        """CLI can output show as JSON."""
        from beads.cli import cli
        import json

        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ['init', '--prefix', 'test'])
            create_result = runner.invoke(cli, ['create', 'JSON show task'])
            issue_id = create_result.output.strip().split()[-1]

            result = runner.invoke(cli, ['show', issue_id, '--json'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['title'] == 'JSON show task'

    def test_version_option(self, runner):
        """CLI shows version."""
        from beads.cli import cli

        result = runner.invoke(cli, ['--version'])
        assert result.exit_code == 0
        assert '0.1.0' in result.output
