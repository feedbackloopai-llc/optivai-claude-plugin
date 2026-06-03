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

## Filter by Repo Label

Beads created inside a git tree carry a `repo:<basename>` label by
convention (see `/bead-create` → "Repo-label convention"). Filter by it:

```bash
# All beads from this plugin
beads list -l repo:optivai-claude-plugin

# All beads from the Pi plugin (cross-cutting view)
beads list -l repo:optivai-pi-plugin

# Combine status + repo filters
beads list --status open -l repo:optivai-claude-plugin

# Multi-label intersection (AND): beads with BOTH labels
beads list -l repo:optivai-claude-plugin -l repo:optivai-pi-plugin
```

Multi-label `-l` flags AND together — the bead must carry every label to
appear in the result. This is what surfaces cross-cutting work like the
HARNESS-RECALL epic.

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
