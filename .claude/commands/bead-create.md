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

## Repo-label convention (2026-06-03+)

Every bead created inside a git working tree must carry a
`repo:<repo-basename>` label so beads can be sorted and filtered by their
source repo without polluting the bead ID. This is the canonical reference
for the convention; other `/bead-*` commands point here.

There are three surfaces that apply the label:

| Surface | Who uses it | How |
|---|---|---|
| Shell wrapper `beads-create` / `bc` | Interactive terminals | `source scripts/shell-aliases.sh` once in `~/.zshrc` |
| `beads_writer.py` hook | Auto-bead creation on tool-use / user-prompt | Already wired — fires reflexively |
| Manual two-step in the Bash tool | Agents calling the raw CLI | Pattern below |

### Two-step pattern for agents (Bash tool, non-interactive)

When an agent calls `beads create` directly via the Bash tool, the wrapper
function is NOT in scope. The agent MUST follow up with `beads label`
immediately:

```bash
# Step 1 — create and capture the new ID.
OUT=$(beads create "Fix race in beads-writer hook" --type bug --priority 1)
echo "$OUT"
ID=$(echo "$OUT" | grep -oE '\b(gz|fblai|optivai)-[a-z0-9]+\b' | head -1)

# Step 2 — apply the repo label if inside a git tree.
if REPO=$(git rev-parse --show-toplevel 2>/dev/null); then
    beads label "$ID" "repo:$(basename "$REPO")"
fi
```

`beads label` is idempotent: re-applying the same label exits 0 with an
informational message. Safe to call defensively.

### Cross-cutting work

A bead that spans multiple repos can carry multiple `repo:` labels — apply
one `beads label` call per repo it touches:

```bash
beads label fblai-idkqk "repo:optivai-claude-plugin"
beads label fblai-idkqk "repo:optivai-pi-plugin"
```

This is how the HARNESS-RECALL epic (`gz-r6ivl`) is back-labeled — see
`AGENTS.md` for the cross-cutting filter examples.

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
