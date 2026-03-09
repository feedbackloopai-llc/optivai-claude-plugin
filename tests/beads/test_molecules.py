"""Tests for molecule workflow system."""
import pytest
import tempfile
from pathlib import Path


class TestMoleculeParser:
    def test_parse_markdown_steps(self):
        """Parse molecule steps from markdown description."""
        from beads.molecules import MoleculeParser

        description = '''
## Step: design
Design the API schema

Needs:
Tier: haiku

## Step: implement
Write the implementation code

Needs: design
Tier: sonnet

## Step: test
Write and run tests

Needs: implement
Tier: haiku
'''
        parser = MoleculeParser()
        steps = parser.parse(description)

        assert len(steps) == 3
        assert steps[0].ref == "design"
        assert steps[0].tier == "haiku"
        assert steps[1].ref == "implement"
        assert "design" in steps[1].needs
        assert steps[2].ref == "test"
        assert "implement" in steps[2].needs

    def test_detect_cycles(self):
        """Detect circular dependencies in molecule."""
        from beads.molecules import MoleculeParser

        description = '''
## Step: a
Step A
Needs: c

## Step: b
Step B
Needs: a

## Step: c
Step C
Needs: b
'''
        parser = MoleculeParser()
        with pytest.raises(ValueError, match="[Cc]ycle"):
            parser.parse(description)

    def test_parse_multiple_dependencies(self):
        """Parse step with multiple dependencies."""
        from beads.molecules import MoleculeParser

        description = '''
## Step: a
First step

## Step: b
Second step

## Step: c
Third step needs both
Needs: a, b
'''
        parser = MoleculeParser()
        steps = parser.parse(description)

        assert len(steps) == 3
        step_c = steps[2]
        assert "a" in step_c.needs
        assert "b" in step_c.needs

    def test_parse_waits_for(self):
        """Parse WaitsFor dynamic conditions."""
        from beads.molecules import MoleculeParser

        description = '''
## Step: deploy
Deploy to staging
Type: wait
WaitsFor: tests_pass, approval_granted
'''
        parser = MoleculeParser()
        steps = parser.parse(description)

        assert len(steps) == 1
        assert steps[0].type == "wait"
        assert "tests_pass" in steps[0].waits_for
        assert "approval_granted" in steps[0].waits_for

    def test_unknown_dependency_error(self):
        """Error when step depends on unknown step."""
        from beads.molecules import MoleculeParser

        description = '''
## Step: a
Step A
Needs: unknown_step
'''
        parser = MoleculeParser()
        with pytest.raises(ValueError, match="unknown"):
            parser.parse(description)

    def test_empty_description(self):
        """Empty description returns empty steps."""
        from beads.molecules import MoleculeParser

        parser = MoleculeParser()
        steps = parser.parse("")
        assert steps == []

        steps = parser.parse(None)
        assert steps == []


class TestMoleculeInstantiator:
    @pytest.fixture
    def temp_beads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / ".beads"

    def test_instantiate_molecule(self, temp_beads):
        """Instantiate molecule creates child issues."""
        from beads.storage import BeadsDatabase
        from beads.molecules import MoleculeInstantiator

        db = BeadsDatabase(temp_beads)

        # Create molecule template
        mol = db.create(
            title="API Setup",
            type="molecule",
            description='''
## Step: design
Design API schema
Tier: haiku

## Step: implement
Build the API
Needs: design
Tier: sonnet
'''
        )

        # Create parent work item
        parent = db.create(title="Build User API")

        # Instantiate
        instantiator = MoleculeInstantiator(db)
        children = instantiator.instantiate(mol, parent)

        assert len(children) == 2
        assert children[0].title == "Design API schema"
        assert children[0].parent == parent.id
        assert children[1].title == "Build the API"
        assert mol.id in children[1].description  # Provenance

    def test_instantiate_wires_dependencies(self, temp_beads):
        """Instantiated steps have correct dependencies."""
        from beads.storage import BeadsDatabase
        from beads.molecules import MoleculeInstantiator

        db = BeadsDatabase(temp_beads)

        mol = db.create(
            title="Sequential Workflow",
            type="molecule",
            description='''
## Step: first
First step

## Step: second
Second step
Needs: first

## Step: third
Third step
Needs: second
'''
        )

        parent = db.create(title="Parent task")
        instantiator = MoleculeInstantiator(db)
        children = instantiator.instantiate(mol, parent)

        # Reload to get updated dependencies
        first = db.get(children[0].id)
        second = db.get(children[1].id)
        third = db.get(children[2].id)

        assert first.id in second.depends_on
        assert second.id in third.depends_on

    def test_instantiate_empty_molecule_fails(self, temp_beads):
        """Instantiating molecule with no steps fails."""
        from beads.storage import BeadsDatabase
        from beads.molecules import MoleculeInstantiator

        db = BeadsDatabase(temp_beads)

        mol = db.create(
            title="Empty Molecule",
            type="molecule",
            description="No steps here"
        )
        parent = db.create(title="Parent")

        instantiator = MoleculeInstantiator(db)
        with pytest.raises(ValueError, match="no steps"):
            instantiator.instantiate(mol, parent)

    def test_instantiate_with_context_variables(self, temp_beads):
        """Template variables are expanded during instantiation."""
        from beads.storage import BeadsDatabase
        from beads.molecules import MoleculeInstantiator

        db = BeadsDatabase(temp_beads)

        mol = db.create(
            title="Parameterized Workflow",
            type="molecule",
            description='''
## Step: create
Create {{entity_name}} endpoint
'''
        )
        parent = db.create(title="Build API")

        instantiator = MoleculeInstantiator(db)
        children = instantiator.instantiate(mol, parent, context={"entity_name": "User"})

        assert "User" in children[0].title
