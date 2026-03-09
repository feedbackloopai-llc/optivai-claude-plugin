"""Tests for beads event emission."""
import pytest
import tempfile
import json
from pathlib import Path


class TestBeadsEventEmitter:
    @pytest.fixture
    def temp_setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            beads_dir = Path(tmpdir) / ".beads"
            log_dir = Path(tmpdir) / "logs"
            beads_dir.mkdir()
            log_dir.mkdir()
            yield beads_dir, log_dir

    def test_emit_bead_created(self, temp_setup):
        """Emits BEAD_CREATED event on issue creation."""
        from beads.events import BeadsEventEmitter
        from beads.storage import BeadsDatabase

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(beads_dir, event_emitter=emitter)

        issue = db.create(title="Test task")

        # Check event was logged
        events = emitter.get_pending_events()
        assert len(events) == 1
        assert events[0]['event_type'] == 'BEAD_CREATED'
        assert events[0]['metadata']['bead_id'] == issue.id

    def test_emit_bead_updated(self, temp_setup):
        """Emits BEAD_UPDATED event on issue update."""
        from beads.events import BeadsEventEmitter
        from beads.storage import BeadsDatabase

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(beads_dir, event_emitter=emitter)

        issue = db.create(title="Original")
        emitter.clear_pending()

        db.update(issue.id, title="Updated", status="in_progress")

        events = emitter.get_pending_events()
        assert len(events) == 1
        assert events[0]['event_type'] == 'BEAD_UPDATED'
        assert 'title' in events[0]['metadata']['changes']

    def test_emit_dependency_added(self, temp_setup):
        """Emits DEPENDENCY_ADDED event when linking issues."""
        from beads.events import BeadsEventEmitter
        from beads.storage import BeadsDatabase

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(beads_dir, event_emitter=emitter)

        parent = db.create(title="Parent")
        child = db.create(title="Child")
        emitter.clear_pending()

        db.add_dependency(child.id, parent.id)

        events = emitter.get_pending_events()
        dep_events = [e for e in events if e['event_type'] == 'DEPENDENCY_ADDED']
        assert len(dep_events) == 1
        assert dep_events[0]['metadata']['source_bead_id'] == child.id
        assert dep_events[0]['metadata']['target_bead_id'] == parent.id

    def test_events_written_to_file(self, temp_setup):
        """Events are written to JSONL log file."""
        from beads.events import BeadsEventEmitter
        from beads.storage import BeadsDatabase
        from datetime import datetime

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(beads_dir, event_emitter=emitter)

        db.create(title="Test task")

        # Check log file exists
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = log_dir / f"beads-events-{date_str}.log"
        assert log_file.exists()

        # Verify content is valid JSONL
        with open(log_file, 'r') as f:
            line = f.readline()
            event = json.loads(line)
            assert event['event_type'] == 'BEAD_CREATED'
            assert event['source_system'] == 'CLAUDE_CODE'

    def test_event_has_pg_compatible_fields(self, temp_setup):
        """Events have fields required for PostgreSQL sync."""
        from beads.events import BeadsEventEmitter
        from beads.storage import BeadsDatabase

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(beads_dir, event_emitter=emitter)

        db.create(title="Test task")

        events = emitter.get_pending_events()
        event = events[0]

        # Required fields for Activity Stream
        assert 'timestamp' in event
        assert 'event_type' in event
        assert 'source_system' in event
        assert 'tenant_id' in event
        assert 'subject_type' in event
        assert 'metadata' in event

    def test_emit_molecule_instantiated(self, temp_setup):
        """Emits MOLECULE_INSTANTIATED event."""
        from beads.events import BeadsEventEmitter

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)

        emitter.emit_molecule_instantiated(
            molecule_id="gz-mol1",
            parent_id="gz-parent1",
            child_ids=["gz-child1", "gz-child2", "gz-child3"]
        )

        events = emitter.get_pending_events()
        assert len(events) == 1
        assert events[0]['event_type'] == 'MOLECULE_INSTANTIATED'
        assert events[0]['metadata']['step_count'] == 3

    def test_emit_bead_closed(self, temp_setup):
        """Emits BEAD_CLOSED event when closing issue."""
        from beads.events import BeadsEventEmitter

        beads_dir, log_dir = temp_setup
        emitter = BeadsEventEmitter(log_dir)

        emitter.emit_bead_closed("gz-test1", reason="Completed")

        events = emitter.get_pending_events()
        assert len(events) == 1
        assert events[0]['event_type'] == 'BEAD_CLOSED'
        assert events[0]['metadata']['reason'] == "Completed"
