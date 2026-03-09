# Bead Show - Show Bead Details

**Purpose**: Display detailed information about a specific bead
**Usage**: `/bead-show <bead-id> [--json]`

## Show Bead Details

```bash
# Replace gz-xxxxx with actual bead ID
beads show gz-xxxxx
```

## JSON Output

```bash
# Get full JSON representation
beads show gz-xxxxx --json
```

## Output Fields

The show command displays:
- **ID**: Unique bead identifier
- **Title**: Bead title/summary
- **Status**: Current status (open, in_progress, done, closed)
- **Type**: Bead type (task, bug, feature, epic, molecule)
- **Priority**: 0 (critical) to 4 (backlog)
- **Description**: Full description text
- **Depends on**: List of prerequisite bead IDs
- **Blocks**: List of beads blocked by this one
- **Parent**: Parent bead ID (if part of hierarchy)
- **Children**: List of child bead IDs

## Example Output

```
ID: gz-abc12
Title: Implement user authentication
Status: in_progress
Type: feature
Priority: 1

Description:
Add JWT-based authentication with refresh tokens

Depends on: gz-xyz99
Blocks: gz-def34, gz-ghi56
```

## Related Commands

- `/bead-list` - List beads
- `/bead-create` - Create new bead
- `/bead-link` - Add dependency
- `/bead-ready` - Show ready beads
