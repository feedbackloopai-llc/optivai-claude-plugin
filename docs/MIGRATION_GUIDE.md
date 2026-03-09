# Migrating from Memory System to Beads

## Overview

Beads is the next-generation knowledge management system for Claude Code, replacing the legacy YAML-based Memory System. It provides:

- **Graph-based tracking**: Issues (beads) with dependencies, hierarchies, and relationships
- **Molecule workflows**: Reusable workflow templates that instantiate as issue graphs
- **Hybrid storage**: Project-level + global beads for flexible organization
- **PostgreSQL sync**: All bead events flow to The Well for analytics
- **Better deduplication**: Content hashing prevents duplicate entries

## Prerequisites

1. Beads CLI installed:
   ```bash
   cd /path/to/optivai-claude-plugin
   pip install -e .
   ```

2. Existing memory data in `~/.claude/gz-observability-memory/`

## Migration Steps

### Step 1: Preview Migration

See what would be migrated without making changes:

```bash
beads migrate --dry-run
```

Sample output:
```
============================================================
BEADS MIGRATION REPORT
============================================================

[DRY RUN - No changes made]

Sources Discovered: 25
  - /Users/you/.claude/gz-observability-memory
  - /Users/you/.claude/logs
  ...

Migration Summary:
  Work Log Entries:  251
  Planned Tasks:     6
  Handoff Contexts:  1
  Project Contexts:  3
  Activity Logs:     0
  ─────────────────────────
  Total:             261
```

### Step 2: Run Migration

```bash
beads migrate
```

This creates beads from:
- `work_log.yaml` entries
- `planned_tasks.yaml` tasks
- `handoff_context.yaml` session info
- Project context files (`*_context.yaml`, `*_project.yaml`)

### Step 3: Verify

Check that beads were created. **Note:** Migrated beads are stored in the **global** database (`~/.claude/beads/`), so use the `--global` flag:

```bash
# List all migrated beads (--global or -g required)
beads list --global --label migrated

# Check counts by type
beads list -g --label work-log | wc -l
beads list -g --label planned-task | wc -l
beads list -g --label context | wc -l

# View details of a specific bead
beads show -g gzg-abc12
```

## What Gets Migrated

| Source | Location | Target Beads |
|--------|----------|--------------|
| `work_log.yaml` | `~/.claude/gz-observability-memory/` | Beads with type=`task`, labels=`[migrated, work-log, {operation}]` |
| `planned_tasks.yaml` | `~/.claude/gz-observability-memory/` | Beads with type=`task`, status mapped |
| `handoff_context.yaml` | `~/.claude/gz-observability-memory/` | Single bead with type=`handoff`, labels=`[migrated, handoff]` |
| Project contexts | `~/.claude/gz-observability-memory/*.yaml` | Beads with type=`task`, labels=`[migrated, context, {project}]` |
| Hook logs (optional) | `~/.claude/logs/agent-activity-*.log` | Beads with type=`task`, labels=`[migrated, activity-log]` |

### Status Mapping

| Old Status | New Bead Status |
|------------|-----------------|
| `pending` | `open` |
| `in_progress` | `in_progress` |
| `completed` | `done` |
| `done` | `done` |

## Migration Options

### Selective Migration

```bash
# Migrate only work log
beads migrate --work-log-only

# Migrate only planned tasks
beads migrate --tasks-only

# Include JSONL hook logs (can be large)
beads migrate --include-logs
```

### Additional Scan Paths

Discover `.claude` folders in additional locations:

```bash
beads migrate --scan-path /path/to/projects --scan-path /another/path
```

### Report Only

Show migration status without running:

```bash
beads migrate --report
```

## Post-Migration

### Old Files Remain

Migration does **not** delete old YAML files. They remain at `~/.claude/gz-observability-memory/` for reference.

### Finding Migrated Data

Migrated beads are stored in the **global** database. Use `-g` or `--global` flag:

```bash
# All migrated beads
beads list -g --label migrated

# Work log entries
beads list -g --label work-log

# Planned tasks
beads list -g --label planned-task

# Project contexts
beads list -g --label context
```

### Working with Beads

**Project beads** (no flag needed):
```bash
# See what's ready to work (project-level)
beads ready

# Create new tasks in current project
beads create "New task to do"

# View/update project bead
beads show gz-abc12
beads update gz-abc12 --status in_progress
beads close gz-abc12
```

**Global beads** (use `-g` flag - includes migrated beads):
```bash
# View migrated bead details
beads show -g gzg-abc12

# List all global beads
beads list -g
```

## Dual-System Coexistence

During transition, both systems can coexist:

1. **Old hooks continue** writing to `~/.claude/logs/` and YAML files
2. **Beads** stores beads in `~/.claude/beads/` (global) and `.beads/` (project)
3. `/prime-agent` checks both sources for pending work

No changes are required to your existing workflow until you're ready to fully migrate.

## Rollback

If you need to revert:

1. Old YAML files remain unchanged at `~/.claude/gz-observability-memory/`
2. Delete the beads database:
   ```bash
   rm -rf ~/.claude/beads/
   rm -rf .beads/  # in project directories
   ```
3. Continue using the legacy memory system

## Idempotent Migration

Migration uses content hashing to prevent duplicates. Running `beads migrate` multiple times:

- Only migrates **new** entries
- Skips already-migrated content
- Safe to run repeatedly

```bash
# First run: migrates 261 items
beads migrate

# Second run: skips everything
beads migrate
# Output: Total: 0 (all already migrated)
```

## Troubleshooting

### "No module named yaml"

Install PyYAML:
```bash
pip install pyyaml
```

### Permission errors

Ensure you have read access to `~/.claude/gz-observability-memory/`

### Migration counts don't match

Some entries may be skipped if:
- They have empty content/descriptions
- They're malformed YAML
- They were already migrated (idempotent check)

Run with `--dry-run` to see details of what would be migrated.

## Architecture Reference

```
Legacy Memory System          Beads System
─────────────────────         ─────────────────────
~/.claude/                    ~/.claude/
├── gz-observability-memory/  ├── beads/           (global beads)
│   ├── work_log.yaml         │   └── issues.jsonl
│   ├── planned_tasks.yaml    │
│   ├── handoff_context.yaml  Project/
│   └── *_context.yaml        └── .beads/          (project beads)
└── logs/                         └── issues.jsonl
    └── agent-activity-*.log
```

Both systems can coexist, with gradual migration recommended.
