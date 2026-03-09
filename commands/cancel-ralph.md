# Cancel Ralph - Stop Active Loop

**Purpose**: Cancel an active Ralph Wiggum iterative development loop
**Usage**: `/cancel-ralph`

## How to Cancel

To cancel the Ralph loop, execute:

```bash
# Check if ralph-loop is active
if [ -f ".claude/ralph-loop.local.md" ]; then
    # Read current iteration
    ITERATION=$(grep '^iteration:' .claude/ralph-loop.local.md | sed 's/iteration: *//')

    # Remove the state file
    rm .claude/ralph-loop.local.md

    echo "Cancelled Ralph loop (was at iteration $ITERATION)"
else
    echo "No active Ralph loop found."
fi
```

## What Happens When Cancelled

1. The `.claude/ralph-loop.local.md` state file is removed
2. The Stop hook will no longer intercept exit attempts
3. Any work completed in previous iterations is preserved in files
4. Git history shows the changes made during the loop

## Checking Loop Status

Before cancelling, you can check the current loop status:

```bash
head -10 .claude/ralph-loop.local.md
```

This shows:
- Current iteration number
- Max iterations configured
- Completion promise (if set)
- When the loop started

## Related Commands

- `/ralph-loop` - Start a new Ralph loop
