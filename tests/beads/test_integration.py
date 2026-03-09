"""End-to-end integration tests for Beads."""
import pytest
import tempfile
import json
from pathlib import Path
from click.testing import CliRunner


class TestEndToEndWorkflow:
    """Test complete workflows."""

    @pytest.fixture
    def workspace(self):
        """Create temporary workspace with beads initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir) / "test-project"
            project.mkdir()
            global_dir = Path(tmpdir) / "global"
            global_dir.mkdir()

            # Initialize beads
            beads_dir = project / ".beads"
            beads_dir.mkdir()

            yield project, global_dir

    def test_molecule_workflow(self, workspace):
        """Test complete molecule workflow: create → pour → execute → close."""
        from beads.storage import BeadsDatabase
        from beads.molecules import MoleculeInstantiator
        from beads.events import BeadsEventEmitter

        project, global_dir = workspace

        # Setup
        log_dir = project / ".claude" / "logs"
        log_dir.mkdir(parents=True)
        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(project / ".beads", event_emitter=emitter)

        # 1. Create molecule template
        mol = db.create(
            title="API Implementation",
            type="molecule",
            description='''
## Step: design
Design the API schema
Tier: haiku

## Step: implement
Implement endpoints
Needs: design
Tier: sonnet

## Step: test
Write tests
Needs: implement
Tier: haiku
'''
        )

        # 2. Create work item
        work = db.create(title="Build User API", type="feature")

        # 3. Pour molecule
        instantiator = MoleculeInstantiator(db)
        steps = instantiator.instantiate(mol, work)

        assert len(steps) == 3

        # 4. Verify dependencies
        design = steps[0]
        implement = steps[1]
        test = steps[2]

        # Reload to get updated dependencies
        design = db.get(design.id)
        implement = db.get(implement.id)
        test = db.get(test.id)

        assert design.id in implement.depends_on
        assert implement.id in test.depends_on

        # 5. Check ready (only design should be ready)
        ready = db.ready()
        ready_ids = [r.id for r in ready]
        assert design.id in ready_ids
        assert implement.id not in ready_ids  # Blocked by design

        # 6. Complete design
        db.update(design.id, status="done")

        # 7. Now implement should be ready
        ready = db.ready()
        ready_ids = [r.id for r in ready]
        assert implement.id in ready_ids

        # 8. Verify events were emitted
        events = emitter.get_pending_events()
        event_types = [e['event_type'] for e in events]
        assert 'BEAD_CREATED' in event_types
        assert 'DEPENDENCY_ADDED' in event_types

    def test_hybrid_storage(self, workspace):
        """Test project + global storage."""
        from beads.manager import BeadsManager

        project, global_dir = workspace

        manager = BeadsManager(project, global_dir)

        # Create global concept
        pattern = manager.create(
            title="Repository Pattern",
            scope="global",
            type="feature",
            description="Standard data access pattern"
        )

        # Create project task that relates to concept
        task = manager.create(
            title="Implement user repository",
            scope="project",
            type="task"
        )

        # Link them
        manager.link(task.id, pattern.id, "implements")

        # Verify storage locations
        assert (project / ".beads" / "issues.jsonl").exists()
        assert (global_dir / "issues.jsonl").exists()

        # Verify link
        links = manager.get_links(task.id)
        assert len(links) == 1
        assert links[0]['target'] == pattern.id

    def test_cli_full_workflow(self, workspace):
        """Test CLI commands in sequence."""
        from beads.cli import cli

        project, global_dir = workspace
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=project):
            # 1. Initialize
            result = runner.invoke(cli, ['init', '--prefix', 'test'])
            assert result.exit_code == 0

            # 2. Create tasks
            result = runner.invoke(cli, ['create', 'Task A'])
            assert result.exit_code == 0
            task_a_id = result.output.strip().split()[-1]

            result = runner.invoke(cli, ['create', 'Task B'])
            assert result.exit_code == 0
            task_b_id = result.output.strip().split()[-1]

            # 3. Add dependency
            result = runner.invoke(cli, ['depend', task_b_id, task_a_id])
            assert result.exit_code == 0

            # 4. Check ready (only Task A should be ready)
            result = runner.invoke(cli, ['ready'])
            assert 'Task A' in result.output
            assert 'Task B' not in result.output

            # 5. Complete Task A
            result = runner.invoke(cli, ['update', task_a_id, '--status', 'done'])
            assert result.exit_code == 0

            # 6. Now Task B should be ready
            result = runner.invoke(cli, ['ready'])
            assert 'Task B' in result.output

            # 7. List as JSON
            result = runner.invoke(cli, ['list', '--json'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 2

    def test_event_emission_to_log_file(self, workspace):
        """Test that events are written to log files for sync."""
        from beads.storage import BeadsDatabase
        from beads.events import BeadsEventEmitter
        from datetime import datetime

        project, global_dir = workspace

        log_dir = project / "logs"
        log_dir.mkdir()

        emitter = BeadsEventEmitter(log_dir)
        db = BeadsDatabase(project / ".beads", event_emitter=emitter)

        # Create and update beads
        issue = db.create(title="Test task")
        db.update(issue.id, status="in_progress")
        db.update(issue.id, status="done")

        # Check log file
        date_str = datetime.now().strftime('%Y-%m-%d')
        log_file = log_dir / f"beads-events-{date_str}.log"
        assert log_file.exists()

        # Read and verify events
        with open(log_file, 'r') as f:
            lines = f.readlines()

        assert len(lines) >= 3  # Created + 2 updates
        events = [json.loads(line) for line in lines]

        # Verify event structure
        for event in events:
            assert 'timestamp' in event
            assert 'event_type' in event
            assert 'source_system' in event
            assert event['source_system'] == 'CLAUDE_CODE'

    def test_memory_bridge_integration(self, workspace):
        """Test memory bridge with beads."""
        from beads.memory_bridge import MemoryBeadsBridge

        project, global_dir = workspace

        bridge = MemoryBeadsBridge(project, global_dir)

        # Simulate TodoWrite sync
        todos = [
            {'id': 't1', 'content': 'Implement feature X', 'status': 'pending'},
            {'id': 't2', 'content': 'Fix bug Y', 'status': 'in_progress'},
            {'id': 't3', 'content': 'Write docs', 'status': 'completed'},
        ]

        synced = bridge.sync_from_todo(todos)
        assert synced == 3

        # Verify beads match todos
        beads = bridge.manager.list(scope="project")
        assert len(beads) == 3

        # Export back to todos
        exported = bridge.sync_to_todo()
        assert len(exported) == 3

        # Check status mapping
        statuses = {t['content']: t['status'] for t in exported}
        assert statuses['Implement feature X'] == 'pending'
        assert statuses['Fix bug Y'] == 'in_progress'
        assert statuses['Write docs'] == 'completed'

    def test_dependency_cascade(self, workspace):
        """Test multi-level dependency resolution."""
        from beads.storage import BeadsDatabase

        project, global_dir = workspace
        db = BeadsDatabase(project / ".beads")

        # Create chain: A → B → C → D
        a = db.create(title="Step A")
        b = db.create(title="Step B")
        c = db.create(title="Step C")
        d = db.create(title="Step D")

        db.add_dependency(b.id, a.id)
        db.add_dependency(c.id, b.id)
        db.add_dependency(d.id, c.id)

        # Only A should be ready
        ready = db.ready()
        assert len(ready) == 1
        assert ready[0].id == a.id

        # Complete A → B becomes ready
        db.update(a.id, status="done")
        ready = db.ready()
        assert len(ready) == 1
        assert ready[0].id == b.id

        # Complete B → C becomes ready
        db.update(b.id, status="done")
        ready = db.ready()
        assert len(ready) == 1
        assert ready[0].id == c.id

        # Complete C → D becomes ready
        db.update(c.id, status="done")
        ready = db.ready()
        assert len(ready) == 1
        assert ready[0].id == d.id

    def test_parallel_dependencies(self, workspace):
        """Test tasks with multiple parallel prerequisites."""
        from beads.storage import BeadsDatabase

        project, global_dir = workspace
        db = BeadsDatabase(project / ".beads")

        # Create diamond: A, B → C (C needs both A and B)
        a = db.create(title="Prereq A")
        b = db.create(title="Prereq B")
        c = db.create(title="Final task")

        db.add_dependency(c.id, a.id)
        db.add_dependency(c.id, b.id)

        # A and B should be ready, C blocked
        ready = db.ready()
        ready_ids = [r.id for r in ready]
        assert a.id in ready_ids
        assert b.id in ready_ids
        assert c.id not in ready_ids

        # Complete A only → C still blocked
        db.update(a.id, status="done")
        ready = db.ready()
        ready_ids = [r.id for r in ready]
        assert c.id not in ready_ids
        assert b.id in ready_ids

        # Complete B → C now ready
        db.update(b.id, status="done")
        ready = db.ready()
        ready_ids = [r.id for r in ready]
        assert c.id in ready_ids
