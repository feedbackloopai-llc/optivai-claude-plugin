# Bead Link - Create Dependency Between Beads

**Purpose**: Add a dependency relationship between beads (child depends on parent)
**Usage**: `/bead-link <child-id> <parent-id>`

## Add Dependency

The child bead will depend on the parent bead (child cannot start until parent is done).

```bash
# Format: beads depend <child-id> <parent-id>
# Child depends on parent (parent must complete first)
beads depend gz-child gz-parent
```

## Example Workflow

```bash
# Create two beads
beads create "Design API schema"       # Creates gz-abc12
beads create "Implement API endpoints"  # Creates gz-def34

# Make implementation depend on design
beads depend gz-def34 gz-abc12

# Now gz-def34 won't appear in "ready" until gz-abc12 is done
beads ready  # Only shows gz-abc12
```

## Check Dependencies

After linking, verify with show command:

```bash
# Check the child bead
beads show gz-def34
# Will show: Depends on: gz-abc12

# Check the parent bead
beads show gz-abc12
# Will show: Blocks: gz-def34
```

## Unblock by Completing Parent

```bash
# Mark parent as done
beads update gz-abc12 --status done

# Now child is ready
beads ready  # Shows gz-def34
```

## Multiple Dependencies

A bead can depend on multiple prerequisites:

```bash
beads depend gz-final gz-step1
beads depend gz-final gz-step2
beads depend gz-final gz-step3
# gz-final won't be ready until all three are done
```

## Related Commands

- `/bead-ready` - Show beads ready to work (not blocked)
- `/bead-show` - View bead dependencies
- `/bead-create` - Create new beads
- `/mol-pour` - Auto-create dependencies from molecule template
