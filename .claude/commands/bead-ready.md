# Bead Ready - Show Beads Ready to Work

**Purpose**: List all beads that are open and not blocked by dependencies
**Usage**: `/bead-ready`

## Show Ready Beads

```bash
beads ready
```

## What Makes a Bead "Ready"

A bead is ready to work when:
1. Status is `open` (not in_progress, done, or closed)
2. All dependencies (beads it depends on) are either `done` or `closed`

## Example Output

```
○ gz-abc12: Design user schema
○ gz-def34: Setup CI/CD pipeline
○ gz-ghi56: Write documentation
```

## Workflow: Pick Ready, Complete, Check Again

```bash
# 1. See what's ready
beads ready

# 2. Start working on one
beads update gz-abc12 --status in_progress

# 3. Complete it
beads update gz-abc12 --status done

# 4. Check what's now ready (previously blocked items may appear)
beads ready
```

## Priority Ordering

Ready beads are sorted by priority:
- Priority 0: Critical (shown first)
- Priority 1: High
- Priority 2: Medium (default)
- Priority 3: Low
- Priority 4: Backlog (shown last)

## The Propulsion Principle

From the Gastown model: "When an agent finds work on their hook, they EXECUTE."

Use `/bead-ready` at the start of a session to find work to do. If there's a ready bead, work on it rather than asking what to do.

## Related Commands

- `/bead-list` - List all beads
- `/bead-show` - Show bead details
- `/bead-link` - Add dependencies
- `/bead-create` - Create new beads
