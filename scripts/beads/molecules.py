"""
ABOUTME: Molecule workflow system for Beads.
ABOUTME: Parses and instantiates workflow templates as issue graphs.
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import BeadsDatabase
    from .models import Issue


@dataclass
class MoleculeStep:
    """A step in a molecule workflow."""
    ref: str                                    # Step reference ID
    title: str = ""                             # Display title
    instructions: str = ""                      # Prose instructions
    needs: List[str] = field(default_factory=list)      # Dependencies
    tier: str = ""                              # haiku, sonnet, opus
    type: str = "task"                          # task, wait
    waits_for: List[str] = field(default_factory=list)  # Dynamic conditions


class MoleculeParser:
    """Parses molecule definitions from markdown."""

    # Regex patterns
    STEP_HEADER = re.compile(r'^##\s*Step:\s*(\S+)\s*$', re.IGNORECASE)
    NEEDS_LINE = re.compile(r'^Needs:\s*(.*)$', re.IGNORECASE)
    TIER_LINE = re.compile(r'^Tier:\s*(haiku|sonnet|opus)\s*$', re.IGNORECASE)
    TYPE_LINE = re.compile(r'^Type:\s*(\w+)\s*$', re.IGNORECASE)
    WAITS_FOR_LINE = re.compile(r'^WaitsFor:\s*(.*)$', re.IGNORECASE)

    def parse(self, description: str) -> List[MoleculeStep]:
        """Parse molecule steps from markdown description."""
        if not description:
            return []

        steps = []
        current_step: Optional[MoleculeStep] = None
        content_lines = []

        for line in description.split('\n'):
            # Check for step header
            match = self.STEP_HEADER.match(line)
            if match:
                # Finalize previous step
                if current_step:
                    self._finalize_step(current_step, content_lines)
                    steps.append(current_step)

                # Start new step
                current_step = MoleculeStep(ref=match.group(1))
                content_lines = []
                continue

            # Accumulate content for current step
            if current_step:
                content_lines.append(line)

        # Finalize last step
        if current_step:
            self._finalize_step(current_step, content_lines)
            steps.append(current_step)

        # Validate
        self._validate(steps)

        return steps

    def _finalize_step(self, step: MoleculeStep, lines: List[str]) -> None:
        """Extract metadata from content lines."""
        instruction_lines = []

        for line in lines:
            stripped = line.strip()

            # Check for metadata lines
            if match := self.NEEDS_LINE.match(stripped):
                deps = [d.strip() for d in match.group(1).split(',') if d.strip()]
                step.needs.extend(deps)
                continue

            if match := self.TIER_LINE.match(stripped):
                step.tier = match.group(1).lower()
                continue

            if match := self.TYPE_LINE.match(stripped):
                step.type = match.group(1).lower()
                continue

            if match := self.WAITS_FOR_LINE.match(stripped):
                conditions = [c.strip() for c in match.group(1).split(',') if c.strip()]
                step.waits_for.extend(conditions)
                continue

            # Regular instruction line
            instruction_lines.append(line)

        step.instructions = '\n'.join(instruction_lines).strip()
        step.title = step.instructions.split('\n')[0] if step.instructions else step.ref

    def _validate(self, steps: List[MoleculeStep]) -> None:
        """Validate molecule structure."""
        step_refs = {s.ref for s in steps}

        # Check all needs references exist
        for step in steps:
            for need in step.needs:
                if need not in step_refs:
                    raise ValueError(f"Step '{step.ref}' depends on unknown step '{need}'")

        # Detect cycles using DFS
        self._detect_cycles(steps)

    def _detect_cycles(self, steps: List[MoleculeStep]) -> None:
        """Detect circular dependencies."""
        deps = {s.ref: s.needs for s in steps}
        visited = set()
        path = []

        def dfs(node: str) -> None:
            if node in path:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                raise ValueError(f"Cycle detected: {' -> '.join(cycle)}")

            if node in visited:
                return

            path.append(node)
            for dep in deps.get(node, []):
                dfs(dep)
            path.pop()
            visited.add(node)

        for step in steps:
            if step.ref not in visited:
                dfs(step.ref)


class MoleculeInstantiator:
    """Instantiates molecule templates as issue graphs."""

    def __init__(self, db: 'BeadsDatabase'):
        self.db = db
        self.parser = MoleculeParser()

    def instantiate(
        self,
        molecule: 'Issue',
        parent: 'Issue',
        context: Dict[str, str] = None
    ) -> List['Issue']:
        """
        Instantiate molecule template as child issues.

        Args:
            molecule: Molecule issue with step definitions
            parent: Parent issue to attach children to
            context: Variable substitution context

        Returns:
            List of created child issues
        """
        # Parse steps from molecule description
        steps = self.parser.parse(molecule.description)
        if not steps:
            raise ValueError("Molecule has no steps defined")

        # Create child issues for each step
        created = []
        step_to_issue: Dict[str, str] = {}

        for step in steps:
            # Expand template variables
            title = self._expand_vars(step.title, context)
            instructions = self._expand_vars(step.instructions, context)

            # Add provenance
            description = f"{instructions}\n\ninstantiated_from: {molecule.id}\nstep: {step.ref}"
            if step.tier:
                description += f"\ntier: {step.tier}"

            # Create child issue
            child = self.db.create(
                title=title,
                type="task",
                description=description,
                priority=parent.priority,
                parent=parent.id
            )

            created.append(child)
            step_to_issue[step.ref] = child.id

        # Wire dependencies based on Needs declarations
        for step in steps:
            if step.needs:
                child_id = step_to_issue[step.ref]
                for need in step.needs:
                    if need in step_to_issue:
                        self.db.add_dependency(child_id, step_to_issue[need])

        return created

    def _expand_vars(self, text: str, context: Dict[str, str] = None) -> str:
        """Expand {{variable}} placeholders."""
        if not context or not text:
            return text

        def replace(match):
            var = match.group(1)
            return context.get(var, match.group(0))

        return re.sub(r'\{\{(\w+)\}\}', replace, text)
