# Bead Update - Update Bead Fields

**Purpose**: Update status, title, priority, or other fields of a bead
**Usage**: `/bead-update <bead-id> [--status STATUS] [--title TITLE] [--priority PRIORITY]`

## Update Status

```bash
# Mark as in progress
beads update gz-abc12 --status in_progress

# Mark as done
beads update gz-abc12 --status done

# Close the bead
beads close gz-abc12
```

## Available Statuses

- `open` - Ready for work (default)
- `in_progress` - Currently being worked on
- `done` - Completed
- `closed` - Permanently closed

## Update Title

```bash
beads update gz-abc12 --title "New improved title"
```

## Update Priority

```bash
# Set to high priority
beads update gz-abc12 --priority 1

# Set to critical
beads update gz-abc12 --priority 0
```

## Priority Levels

- `0` - Critical
- `1` - High
- `2` - Medium (default)
- `3` - Low
- `4` - Backlog

## Assign to Someone

```bash
beads update gz-abc12 --assignee "agent-name"
```

## Multiple Updates

```bash
# Update multiple fields at once
beads update gz-abc12 --status in_progress --priority 1 --assignee "claude"
```

## Related Commands

- `/bead-show` - View current bead state
- `/bead-list` - List beads by status
- `/bead-ready` - Check what's unblocked
