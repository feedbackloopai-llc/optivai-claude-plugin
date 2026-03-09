"""
Tests for YAML-to-Beads migration.
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.beads.migrate import MemoryMigrator
from scripts.beads.storage import BeadsDatabase


class TestMemoryMigrator:
    """Tests for MemoryMigrator class."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        with tempfile.TemporaryDirectory() as beads_tmpdir:
            with tempfile.TemporaryDirectory() as memory_tmpdir:
                with tempfile.TemporaryDirectory() as project_tmpdir:
                    yield {
                        'beads': Path(beads_tmpdir),
                        'memory': Path(memory_tmpdir),
                        'project': Path(project_tmpdir),
                    }

    @pytest.fixture
    def migrator(self, temp_dirs):
        """Create migrator with temp directories."""
        return MemoryMigrator(
            beads_dir=temp_dirs['beads'],
            project_dir=temp_dirs['project'],
        )

    @pytest.fixture
    def sample_work_log(self, temp_dirs):
        """Create sample work_log.yaml."""
        work_log = {
            'last_updated': '2026-02-05T10:00:00+00:00',
            'entry_count': 3,
            'entries': [
                {
                    'timestamp': '2026-02-05T09:00:00+00:00',
                    'local_time': '2026-02-05 09:00:00',
                    'operation': 'bash',
                    'description': 'Run tests',
                    'details': {'command': 'pytest tests/', 'exit_code': 0},
                },
                {
                    'timestamp': '2026-02-05T09:05:00+00:00',
                    'local_time': '2026-02-05 09:05:00',
                    'operation': 'write',
                    'description': 'Create migration script',
                    'details': {'file': 'scripts/migrate.py'},
                },
                {
                    'timestamp': '2026-02-05T09:10:00+00:00',
                    'local_time': '2026-02-05 09:10:00',
                    'operation': 'user_prompt',
                    'description': 'Fix the failing test',
                    'details': {},
                },
            ],
        }
        work_log_file = temp_dirs['memory'] / 'work_log.yaml'
        with open(work_log_file, 'w') as f:
            yaml.dump(work_log, f)
        return work_log_file

    @pytest.fixture
    def sample_planned_tasks(self, temp_dirs):
        """Create sample planned_tasks.yaml."""
        tasks = {
            'last_synced': '2026-02-05T10:00:00+00:00',
            'pending': [
                {'content': 'Implement feature X', 'active_form': 'Implementing X'},
            ],
            'in_progress': [
                {'content': 'Fix bug Y', 'active_form': 'Fixing Y', 'summary': 'Auth issue'},
            ],
            'completed': [
                {'content': 'Setup project', 'active_form': 'Setting up', 'added_at': '2026-02-01T10:00:00+00:00'},
            ],
        }
        tasks_file = temp_dirs['memory'] / 'planned_tasks.yaml'
        with open(tasks_file, 'w') as f:
            yaml.dump(tasks, f)
        return tasks_file

    @pytest.fixture
    def sample_handoff(self, temp_dirs):
        """Create sample handoff_context.yaml."""
        handoff = {
            'from_session': 'session-20260205-090000-abc123',
            'handoff_created': '2026-02-05T10:00:00+00:00',
            'current_state': {
                'cwd': '/Users/test/project',
                'project': 'test-project',
                'focus': 'migration',
            },
            'recent_work': [
                {'time': '09:55', 'operation': 'bash', 'description': 'Run tests'},
                {'time': '09:58', 'operation': 'edit', 'description': 'Update code'},
            ],
            'what_was_working_on': 'Building migration script',
        }
        handoff_file = temp_dirs['memory'] / 'handoff_context.yaml'
        with open(handoff_file, 'w') as f:
            yaml.dump(handoff, f)
        return handoff_file

    @pytest.fixture
    def sample_context(self, temp_dirs):
        """Create sample project context file."""
        context = {
            'project': 'NCCDP',
            'last_updated': '2026-01-26T12:00:00',
            'program_ids': {
                'CDP': 'uuid-1',
                'CADDCT': 'uuid-2',
            },
        }
        context_file = temp_dirs['memory'] / 'nccdp_kpi_context.yaml'
        with open(context_file, 'w') as f:
            yaml.dump(context, f)
        return context_file

    def test_migrate_work_log_entry(self, migrator, sample_work_log, temp_dirs):
        """Work log entry becomes a bead with correct metadata."""
        count, details = migrator.migrate_work_log(temp_dirs['memory'])

        assert count == 3
        assert len(details) == 3

        # Check first entry
        first = details[0]
        assert first['type'] == 'work-log-entry'
        assert first['operation'] == 'bash'
        assert 'bead_id' in first

        # Verify bead was created
        bead = migrator.manager.get(first['bead_id'])
        assert bead is not None
        assert 'migrated' in bead.labels
        assert 'work-log' in bead.labels
        assert 'bash' in bead.labels

    def test_migrate_planned_task_status_mapping(self, migrator, sample_planned_tasks, temp_dirs):
        """Todo status maps to bead status correctly."""
        count, details = migrator.migrate_planned_tasks(temp_dirs['memory'])

        assert count == 3  # 1 pending + 1 in_progress + 1 completed

        # Find each task
        pending_detail = next(d for d in details if d['original_status'] == 'pending')
        in_progress_detail = next(d for d in details if d['original_status'] == 'in_progress')
        completed_detail = next(d for d in details if d['original_status'] == 'completed')

        # Check status mapping
        pending_bead = migrator.manager.get(pending_detail['bead_id'])
        assert pending_bead.status.value == 'open'

        in_progress_bead = migrator.manager.get(in_progress_detail['bead_id'])
        assert in_progress_bead.status.value == 'in_progress'

        completed_bead = migrator.manager.get(completed_detail['bead_id'])
        assert completed_bead.status.value == 'done'

    def test_migrate_preserves_timestamps(self, migrator, sample_work_log, temp_dirs):
        """Original timestamps preserved in bead metadata."""
        count, details = migrator.migrate_work_log(temp_dirs['memory'])

        first_detail = details[0]
        bead = migrator.manager.get(first_detail['bead_id'])

        # Timestamp should be in description
        assert '2026-02-05T09:00:00' in bead.description
        assert first_detail['timestamp'] == '2026-02-05T09:00:00+00:00'

    def test_dry_run_no_writes(self, migrator, sample_work_log, temp_dirs):
        """Dry run doesn't create any beads."""
        count, details = migrator.migrate_work_log(temp_dirs['memory'], dry_run=True)

        assert count == 3
        assert len(details) == 3

        # No bead IDs should be present (not created)
        for detail in details:
            assert 'bead_id' not in detail

        # Beads database should be empty
        all_beads = migrator.manager.list(scope='all')
        assert len(all_beads) == 0

    def test_idempotent_migration(self, migrator, sample_work_log, temp_dirs):
        """Running migration twice doesn't duplicate beads."""
        # First migration
        count1, details1 = migrator.migrate_work_log(temp_dirs['memory'])
        assert count1 == 3

        # Second migration (same data)
        count2, details2 = migrator.migrate_work_log(temp_dirs['memory'])
        assert count2 == 0  # Nothing new to migrate

        # Total beads should still be 3
        all_beads = migrator.manager.list(scope='all')
        assert len(all_beads) == 3

    def test_migrate_handoff(self, migrator, sample_handoff, temp_dirs):
        """Handoff context becomes a single bead."""
        count, details = migrator.migrate_handoff(temp_dirs['memory'])

        assert count == 1
        assert len(details) == 1

        detail = details[0]
        assert detail['type'] == 'handoff'
        assert detail['session'] == 'session-20260205-090000-abc123'
        assert 'bead_id' in detail

        # Verify bead
        bead = migrator.manager.get(detail['bead_id'])
        assert bead is not None
        assert 'handoff' in bead.labels
        assert 'session-20260205-090000-abc123' in bead.title

    def test_migrate_project_context(self, migrator, sample_context, temp_dirs):
        """Project context files become beads."""
        count, details = migrator.migrate_project_contexts(temp_dirs['memory'])

        assert count == 1
        assert len(details) == 1

        detail = details[0]
        assert detail['type'] == 'context'
        assert 'Nccdp' in detail['project']  # Project name extracted from filename
        assert 'bead_id' in detail

        # Verify bead
        bead = migrator.manager.get(detail['bead_id'])
        assert bead is not None
        assert 'context' in bead.labels
        assert 'migrated' in bead.labels

    def test_migrate_all(self, migrator, sample_work_log, sample_planned_tasks,
                         sample_handoff, sample_context, temp_dirs):
        """Full migration handles all sources."""
        # Monkey-patch the global memory path for testing
        original_migrate_work_log = migrator.migrate_work_log
        original_migrate_planned_tasks = migrator.migrate_planned_tasks
        original_migrate_handoff = migrator.migrate_handoff
        original_migrate_contexts = migrator.migrate_project_contexts

        def patched_work_log(source_dir=None, dry_run=False):
            return original_migrate_work_log(temp_dirs['memory'], dry_run)

        def patched_planned_tasks(source_dir=None, dry_run=False):
            return original_migrate_planned_tasks(temp_dirs['memory'], dry_run)

        def patched_handoff(source_dir=None, dry_run=False):
            return original_migrate_handoff(temp_dirs['memory'], dry_run)

        def patched_contexts(source_dir=None, dry_run=False):
            return original_migrate_contexts(temp_dirs['memory'], dry_run)

        migrator.migrate_work_log = patched_work_log
        migrator.migrate_planned_tasks = patched_planned_tasks
        migrator.migrate_handoff = patched_handoff
        migrator.migrate_project_contexts = patched_contexts

        report = migrator.migrate_all(dry_run=True)

        assert report['dry_run'] is True
        assert report['totals']['work_log'] == 3
        assert report['totals']['planned_tasks'] == 3
        assert report['totals']['handoff'] == 1
        assert report['totals']['contexts'] == 1
        assert report['totals']['total'] == 8

    def test_generate_report(self, migrator, temp_dirs):
        """Report generation produces readable output."""
        # Create minimal migration result
        result = {
            'dry_run': True,
            'sources_discovered': ['/Users/test/.claude/gz-observability-memory'],
            'migrations': {},
            'totals': {
                'work_log': 10,
                'planned_tasks': 5,
                'handoff': 1,
                'contexts': 2,
                'logs': 0,
                'total': 18,
            },
            'errors': [],
        }

        report = migrator.generate_report(result)

        assert 'BEADS MIGRATION REPORT' in report
        assert 'DRY RUN' in report
        assert 'Work Log Entries:  10' in report
        assert 'Total:             18' in report

    def test_discover_sources(self, migrator, temp_dirs):
        """Source discovery finds memory directories."""
        # Create a mock .claude structure
        claude_dir = temp_dirs['project'] / '.claude'
        memory_dir = claude_dir / 'gz-observability-memory'
        memory_dir.mkdir(parents=True)

        sources = migrator.discover_all_memory_sources([temp_dirs['project']])

        # Should find the mock directory
        assert any(str(memory_dir) in str(s) for s in sources)

    def test_content_hash_deduplication(self, migrator, temp_dirs):
        """Content hashing prevents duplicates."""
        # Same content should produce same hash
        hash1 = migrator._content_hash("test content")
        hash2 = migrator._content_hash("test content")
        assert hash1 == hash2

        # Different content should produce different hash
        hash3 = migrator._content_hash("different content")
        assert hash1 != hash3

    def test_migrate_hook_logs(self, migrator, temp_dirs):
        """Hook logs (JSONL) are migrated."""
        # Create mock log file
        logs_dir = temp_dirs['memory']
        log_file = logs_dir / 'agent-activity-2026-02-05.log'

        log_entries = [
            {
                'timestamp': '2026-02-05T09:00:00+00:00',
                'operation': 'bash',
                'prompt': 'Run tests',
                'session_id': 'session-abc',
                'project': 'test',
            },
            {
                'timestamp': '2026-02-05T09:05:00+00:00',
                'operation': 'user_prompt',
                'prompt': 'Fix the bug',
                'session_id': 'session-abc',
                'project': 'test',
            },
        ]

        with open(log_file, 'w') as f:
            for entry in log_entries:
                f.write(json.dumps(entry) + '\n')

        count, details = migrator.migrate_hook_logs(logs_dir)

        assert count == 2
        assert len(details) == 2
        assert all('bead_id' in d for d in details)

    def test_migrate_empty_directory(self, migrator, temp_dirs):
        """Migration handles empty directories gracefully."""
        empty_dir = temp_dirs['memory'] / 'empty'
        empty_dir.mkdir()

        count, details = migrator.migrate_work_log(empty_dir)
        assert count == 0
        assert len(details) == 0

    def test_migrate_malformed_yaml(self, migrator, temp_dirs):
        """Migration handles malformed YAML gracefully."""
        bad_file = temp_dirs['memory'] / 'work_log.yaml'
        with open(bad_file, 'w') as f:
            f.write("not: valid: yaml: content: [[[")

        count, details = migrator.migrate_work_log(temp_dirs['memory'])
        assert count == 0
