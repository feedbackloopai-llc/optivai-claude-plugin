# Bead Create - Create New Bead

**Purpose**: Create a new bead (issue) in the knowledge graph
**Usage**: `/bead-create <title> [--type task|bug|feature|epic|molecule] [--priority 0-4]`

## Create Task (Default)

```bash
# Initialize beads if not already done
if [ ! -d ".beads" ]; then
    beads init
fi

# Create a new task bead
# Replace "Your task title" with actual title
beads create "Your task title"
```

## Create with Type and Priority

```bash
# Create a high-priority bug
beads create "Fix login validation" --type bug --priority 1

# Create an epic for feature grouping
beads create "User Authentication System" --type epic --priority 0

# Create a molecule (workflow template)
beads create "API Implementation Workflow" --type molecule
```

## Create with Description

```bash
# Create task with description
beads create "Implement user registration" --type feature --description "Add registration endpoint with email validation"
```

## After Creation

After creating a bead, you can:
- Link it to other beads: `/bead-link`
- View it: `/bead-show <id>`
- List all beads: `/bead-list`
- Check what's ready to work: `/bead-ready`

## Related Commands

- `/bead-list` - List beads
- `/bead-show` - Show bead details
- `/bead-link` - Link beads together
- `/bead-ready` - Show beads ready to work
- `/mol-pour` - Instantiate molecule workflow
