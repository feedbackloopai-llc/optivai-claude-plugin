# Bead List - List Beads in Knowledge Graph

**Purpose**: List beads (issues) with optional filters
**Usage**: `/bead-list [--status open|in_progress|done|closed] [--type task|bug|feature|epic|molecule] [--json]`

## List All Open Beads

```bash
beads list --status open
```

## List All Beads

```bash
beads list
```

## Filter by Type

```bash
# List all bugs
beads list --type bug

# List all molecules (workflow templates)
beads list --type molecule

# List all features
beads list --type feature
```

## Filter by Status

```bash
# List in-progress items
beads list --status in_progress

# List completed items
beads list --status done

# List closed items
beads list --status closed
```

## JSON Output (for processing)

```bash
# Get JSON output for programmatic use
beads list --json

# Filter and get JSON
beads list --status open --type task --json
```

## Status Icons

- `○` - Open
- `◐` - In Progress
- `●` - Done
- `✓` - Closed
- `⚓` - Hooked (attached to agent)
- `📌` - Pinned (permanent record)

## Related Commands

- `/bead-create` - Create new bead
- `/bead-show` - Show bead details
- `/bead-ready` - Show beads ready to work
