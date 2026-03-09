# OptivAI Claude Code Plugin

A plugin for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that automatically logs all AI assistant activity to PostgreSQL, enabling observability, analytics, and audit trails for AI-assisted development.

## What is This?

### What is Claude Code?

[Claude Code](https://claude.ai/claude-code) is Anthropic's AI coding assistant that runs in your terminal. It can:
- Read and write files in your codebase
- Execute shell commands
- Search code with grep/glob
- Create commits and pull requests
- Perform complex multi-step coding tasks

Think of it as having a senior developer pair-programming with you who can actually edit your files.

### What Does This Plugin Do?

This plugin adds **automatic activity logging** to Claude Code:

1. **Captures Everything**: Every tool operation (file reads, writes, bash commands) and every prompt you send is logged
2. **Git Repository Attribution**: Automatically detects the git repository by parsing `git remote get-url origin`, capturing the full repo path (e.g., `feedbackloopai/optivai-claude-plugin`), organization, and repo name
3. **Stores Locally First**: Logs are written to local JSON files in each project
4. **Syncs to PostgreSQL**: A background daemon syncs logs to Neon PostgreSQL
5. **Enables Analytics**: Query your AI usage patterns by repository, audit what changes were made, understand productivity

### Why Would I Want This?

- **Audit Trail**: Know exactly what the AI did to your codebase
- **Productivity Analytics**: See which projects you work on most, what operations are common
- **Session Continuity**: When you come back to work, the AI can read its own history
- **Team Visibility**: Multiple developers' activity flows into shared PostgreSQL tables
- **Compliance**: Maintain records of AI-assisted code changes

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Claude Code CLI                              │
│                                                                      │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐             │
│  │ User Prompt │    │ Tool: Read  │    │ Tool: Write │    ...      │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘             │
│         │                  │                  │                     │
│         ▼                  ▼                  ▼                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Claude Code Hooks                         │   │
│  │  • UserPromptSubmit → user_prompt_submit.py                 │   │
│  │  • PreToolUse → pre_tool_use.py                             │   │
│  │  • Stop → session_summary.py (token usage + cost tracking)  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Local Log Files (JSON)      │
                    │   ~/.claude/logs/             │
                    │   agent-activity-YYYY-MM-DD.log│
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   PostgreSQL Sync Daemon       │
                    │   pg_sync.py           │
                    │   (runs every 60 seconds)     │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   PostgreSQL Database        │
                    │   DW_DEV_STREAM.LANDING       │
                    │   .RAW_EVENTS (instant)       │
                    └───────────────────────────────┘
                                    │
                                    ▼ (15-minute refresh)
                    ┌───────────────────────────────┐
                    │   Activity Stream             │
                    │   DW_DEV_STREAM.ACTIVITY      │
                    │   .ACTIVITY_STREAM            │
                    └───────────────────────────────┘
```

### Data Flow Timing

| Stage | Latency | Description |
|-------|---------|-------------|
| **Local Logs** | Instant | Written immediately by hooks |
| **RAW_EVENTS** | ~60 seconds | Sync daemon pushes every minute |
| **ACTIVITY_STREAM** | ~15 minutes | Automatic refresh cycle |

**Important**: When querying your activity, use `RAW_EVENTS` for real-time data or `ACTIVITY_STREAM` for the unified, normalized view (with 15-minute lag).

### Sync Daemon Features

The `pg_sync.py` daemon includes:

| Feature | Description |
|---------|-------------|
| **Historical Backfill** | Automatically processes all historical log files, not just today's |
| **Deep Scanning** | Recursively scans up to 5 levels deep to find projects with `.claude/logs` |
| **Smart Resumption** | Tracks position per file, resumes from last sync point after restart |
| **Crash Recovery** | Auto-restarts via launchd if the process crashes |
| **Efficient Filtering** | Skips `node_modules`, `__pycache__`, `.git`, `venv` directories |

**Default Scan Paths:**
- `~/Development` (primary dev directory)
- `~/Development/Migrations` (migration projects)
- `~/Documents`
- `~/Projects`
- `~/Code`
- Current working directory

To add custom scan paths, use the `--scan-path` argument:
```bash
python3 ~/.claude/hooks/pg_sync.py --scan-path /path/to/projects
```

---

## Quick Start Guide

### Prerequisites

- **Claude Code** installed ([Installation Guide](https://docs.anthropic.com/en/docs/claude-code/getting-started))
- **Python 3.8+** with pip
- **PostgreSQL account** with key-pair authentication configured
- **Supported Platforms**: Windows, macOS, Linux

### Step 1: Clone the Repository

```bash
cd ~/Documents/optivai  # or your preferred location
git clone https://github.com/feedbackloopai/optivai-claude-plugin.git
cd optivai-claude-plugin
```

### Step 2: Install Python Dependencies

```bash
pip install -r scripts/requirements.txt
```

Required packages:
- `psycopg2-binary` - PostgreSQL database connector
- `cryptography` - For key-pair authentication

### Step 3: Run the Install Script

The install script handles everything: hooks, commands, agents, and the sync daemon. The script automatically detects your operating system.

#### Windows Installation

**Option A: PowerShell (Recommended)**
```powershell
.\scripts\install.ps1
```

**Option B: Git Bash / MSYS2**
```bash
./scripts/install.sh
# Automatically detects Windows and launches PowerShell installer
```

#### macOS / Linux Installation

```bash
./scripts/install.sh
```

#### Installation Options

| Option | Description |
|--------|-------------|
| `--skip-daemon` | Skip background daemon installation |
| `--force` | Overwrite existing installation |
| `--uninstall` | Remove the installation (keeps logs and user data) |
| `--help` | Show help message |

This installs:
- Hook scripts to `~/.claude/hooks/` (including `open_brain.py`, `brain_hook.py`, `till_done.py`)
- Slash commands to `~/.claude/commands/` (including `superpowers/` subdirectory)
- Agent templates to `~/.claude/agents/`
- Memory schema DDL to `~/.claude/sql/` (for `--init` post-install)
- Configuration template (if not exists)
- PostgreSQL sync daemon:
  - **Windows**: Task Scheduler task
  - **macOS**: launchd agent
  - **Linux**: systemd user service (or cron fallback)

### Step 4: Configure PostgreSQL Credentials

Edit `~/.claude/hooks/auto-logger-config.json` with your PostgreSQL credentials:

```json
{
  "logging": {
    "enabled": true,
    "log_dir": "~/.claude/logs",
    "log_tool_operations": true,
    "log_user_prompts": true,
    "max_prompt_length": 500,
    "session_tracking": true
  },
  "destinations": {
    "postgresql": {
      "enabled": true,
      "account": "YOUR_ACCOUNT.us-east-1",
      "warehouse": "COMPUTE_WH",
      "role": "YOUR_ROLE",
      "target_table": "DW_DEV_STREAM.LANDING.RAW_EVENTS",
      "tenant_id": "CLAUDE_CODE",
      "source_system": "CLAUDE_CODE",
      "auth": {
        "type": "key_pair",
        "user": "YOUR_PG_USER",
        "private_key_path": "~/.postgresql/your_key.p8"
      },
      "sync": {
        "batch_size": 100,
        "flush_interval_seconds": 60,
        "retry_attempts": 3,
        "retry_delay_seconds": 5
      }
    }
  }
}
```

### Step 5: Configure Claude Code Hooks

Add to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/pre_tool_use.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/user_prompt_submit.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/session_summary.py"
          },
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/stop-hook.sh"
          }
        ]
      }
    ]
  }
}
```

### Step 6: Verify Installation

The sync daemon starts automatically. Verify with:

```bash
# Check daemon status
launchctl list | grep claude-pg

# Check sync status
python3 ~/.claude/hooks/pg_sync.py --status
```

### Sync Daemon Setup

**Option A: Auto-Start with macOS Launch Agent (Recommended)**

The Launch Agent ensures the sync daemon runs continuously and survives reboots.

```bash
# Install as a Launch Agent (starts automatically at login)
./scripts/install-launchd.sh

# To uninstall later
./scripts/install-launchd.sh --uninstall
```

The Launch Agent will:
- Start automatically when you log in
- Restart automatically if it crashes (with 60-second throttle)
- Run in the background with low priority (nice level 10)
- Log output to `~/.claude/logs/sync_daemon_*.log`
- Scan all default paths plus nested project directories
- Backfill historical logs that were missed

**Option B: Manual Start**

```bash
# Start background sync manually (detached process)
python3 scripts/pg_sync.py --daemon

# Or run a single sync pass (good for testing/backfill)
python3 scripts/pg_sync.py --once

# Preview what would sync without writing to PostgreSQL
python3 scripts/pg_sync.py --dry-run

# Check sync status and see tracked projects
python3 scripts/pg_sync.py --status
```

**Daemon Management Commands:**
```bash
# Check if daemon is running (macOS)
launchctl list | grep claude-pg

# Stop daemon
launchctl stop com.feedbackloopai.claude-pg-sync

# Unload daemon (stops and prevents auto-start)
launchctl unload ~/Library/LaunchAgents/com.feedbackloopai.claude-pg-sync.plist

# Load daemon (starts and enables auto-start)
launchctl load ~/Library/LaunchAgents/com.feedbackloopai.claude-pg-sync.plist

# View real-time logs
tail -f ~/.claude/logs/sync_daemon_stdout.log

# View errors
tail -f ~/.claude/logs/sync_daemon_stderr.log

# Check sync state (shows last position per project)
cat ~/.claude/logs/.sync_state.json | python3 -m json.tool
```

**Troubleshooting Sync Issues:**
```bash
# Force a manual sync to test
python3 ~/.claude/hooks/pg_sync.py --once --verbose

# Reset sync state (will re-sync all historical logs)
rm ~/.claude/logs/.sync_state.json

# Check which projects are being found
python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / '.claude/hooks'))
from pg_sync import ProjectScanner, SyncService
service = SyncService()
projects = service.scanner.find_projects_with_logs()
print(f'Found {len(projects)} projects:')
for p in projects:
    print(f'  {p}')
"
```

### Step 7: Verify It's Working

1. Start Claude Code in any project: `claude`
2. Send a message or let Claude read a file
3. Check local logs: `ls ~/.claude/logs/`
4. Check sync status: `python3 scripts/pg_sync.py --status`
5. Query PostgreSQL:
   ```sql
   SELECT * FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
   WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
   ORDER BY EVENT_AT DESC
   LIMIT 10;
   ```

---

## Slash Commands

This plugin includes slash commands for Claude Code. These are shortcuts you can type in Claude Code to perform common operations.

### Available Commands

#### Activity & Session Commands

| Command | Description |
|---------|-------------|
| `/prime-agent [path]` | Full context priming with Memory System + Activity Logs + Propulsion Check. Optional: specify a directory path to focus on. |
| `/handoff [summary]` | Prepare structured handoff context for session continuity. Use before ending a session. |
| `/quick-context` | Fast context loading (one-liner) |
| `/context-check` | Verify agent has proper context |
| `/load-context` | Load multi-day historical context |
| `/summary` | Session activity summary |
| `/new-session` | Start fresh session with new ID |
| `/sync-status` | Check PostgreSQL sync status |
| `/sync-now` | Manually trigger sync |
| `/activity-query` | Query activity from PostgreSQL |
| `/export-context` | Export activity to file |
| `/config-logs` | View/modify logging configuration |
| `/db-connect` | Test database connections |
| `/datafix-log` | Log data operations to DataFixLog |
| `/create-prp` | Create Product Requirement Prompt |

#### JIRA Integration Commands

| Command | Description |
|---------|-------------|
| `/jira test` | Test JIRA connection and show config |
| `/jira create "Title"` | Create a new JIRA ticket |
| `/jira view DA-123` | View ticket details |
| `/jira assign DA-123 username` | Assign ticket to user |
| `/jira comment DA-123 "text"` | Add comment to ticket |
| `/jira transition DA-123 "Done"` | Change ticket status |
| `/jira search "JQL query"` | Search tickets with JQL |

#### Reporting Commands

| Command | Description |
|---------|-------------|
| `/report` | Query DW_DEV_REPORT.RPT views |
| `/report jira` | Query JIRA data from PostgreSQL |
| `/report costs` | View Claude Code cost estimates |

#### Memory System Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/memory-status` | `mstat` | Show status of all memory files |
| `/memory-read` | `mread` | Read specific or all memory files |
| `/memory-checkpoint` | `mcp` | Create recovery checkpoint |
| `/memory-add-task` | `mtask` | Add task to planned tasks |
| `/memory-complete-task` | `mdone` | Remove completed task |
| `/memory-log` | `mlog` | Add work log entry |
| `/memory-prime` | `mprime` | Get full memory context |

#### Ralph Loop Commands

| Command | Description |
|---------|-------------|
| `/ralph-loop` | Start an iterative development loop |
| `/cancel-ralph` | Cancel an active Ralph loop |

### Installing Slash Commands

Copy the commands to your Claude Code commands directory:

```bash
# Create commands directory if it doesn't exist
mkdir -p ~/.claude/commands

# Copy all command files
cp .claude/commands/*.md ~/.claude/commands/
cp .claude/commands/*.py ~/.claude/commands/
cp .claude/commands/*.json ~/.claude/commands/
```

### Using Slash Commands

In Claude Code, simply type the command:

```
/prime-agent
```

Claude will execute the command and provide the output.

#### Commands with Optional Arguments

Some commands accept optional arguments. For example, `/prime-agent` accepts an optional directory path:

```bash
# Prime with current working directory (default)
/prime-agent

# Prime with a specific project directory
/prime-agent /path/to/project

# Prime with a subdirectory of the current project
/prime-agent ./src/components

# Prime with a home-relative path
/prime-agent ~/Documents/optivai/my-project
```

When a directory path is provided:

- Git status and history are retrieved from that directory
- README.md and CLAUDE.md are read from that directory
- The agent focuses its context and responses on that specific codebase
- **Token Efficiency**: All file reads and searches are restricted to the target directory, preventing unnecessary token consumption from reading files outside the specified scope
- Files that don't exist in the target directory are skipped (no searching in parent directories)

---

## Memory System (Legacy)

> **DEPRECATED**: The YAML-based Memory System is being replaced by **Beads**.
> Use `beads migrate` to migrate your existing data. See [Beads](#beads-graph-based-knowledge-system) for the new system.

The plugin includes a **Persistent Memory System** that provides session continuity across Claude Code sessions. This enables agents to:

- Resume work after disconnections or crashes
- Track planned tasks across sessions
- Maintain a chronological work log
- Create recovery checkpoints before complex operations

### Memory System Architecture

```
~/.claude/gz-observability-memory/
├── session_state.yaml      # Current session context and focus
├── planned_tasks.yaml      # Tasks to complete (remove when done)
├── work_log.yaml           # Chronological action history
└── recovery_checkpoint.yaml # Crash recovery context
```

### Memory System Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/memory-status` | `mstat` | Show status of all memory files |
| `/memory-read` | `mread` | Read a specific memory file or all files |
| `/memory-checkpoint` | `mcp`, `checkpoint` | Create a recovery checkpoint |
| `/memory-add-task` | `mtask`, `add-task` | Add a task to planned tasks |
| `/memory-complete-task` | `mdone`, `complete-task` | Mark task as complete (removes it) |
| `/memory-log` | `mlog`, `work-log` | Add entry to work log |
| `/memory-prime` | `mprime`, `prime` | Get full memory context for agent priming |

### Memory File Formats

**session_state.yaml** - Current session context:
```yaml
session:
  id: "session_20251218_001"
  started_at: "2025-12-18T10:00:00Z"
  project_path: "/path/to/project"
  status: "in_progress"
  last_updated: "2025-12-18T10:30:00Z"

current_focus:
  task: "Implementing memory system"
  phase: "Development"

context:
  user: "Chris Hughes"
  project: "OptivAI Claude Plugin"
```

**planned_tasks.yaml** - Tasks to complete:
```yaml
tasks:
  - id: "abc123"
    description: "Add memory system commands"
    priority: "high"
    status: "pending"
    added_at: "2025-12-18T10:00:00Z"
  - id: "def456"
    description: "Update documentation"
    priority: "medium"
    status: "pending"
    added_at: "2025-12-18T10:05:00Z"
```

**work_log.yaml** - Action history:
```yaml
entries:
  - timestamp: "2025-12-18T10:15:00Z"
    action: "Added MemorySystemManager class"
    details: "Implemented YAML read/write with graceful degradation"
    project: "optivai-claude-plugin"
  - timestamp: "2025-12-18T10:30:00Z"
    action: "Added memory commands"
    details: "7 new commands for memory management"
    project: "optivai-claude-plugin"
```

**recovery_checkpoint.yaml** - Crash recovery:
```yaml
checkpoint:
  timestamp: "2025-12-18T10:30:00Z"
  status: "in_progress"

project:
  path: "/Users/user/project"
  name: "my-project"
  state: "in_progress"

what_was_working_on: "Implementing feature X"
context_for_resume: "Left off at step 3 of 5"
```

### Using the Memory System

**Agent Priming with Memory:**
```bash
# Prime with current directory
/prime-agent

# Prime with a specific project (optional argument)
/prime-agent /path/to/project
```
This reads both the Memory System files AND the Activity Logs (hooks) to provide complete context. The optional directory argument focuses the priming on a specific project or subdirectory.

**Before Complex Operations:**
```
/memory-checkpoint --description "Starting database migration" --status in_progress
```

**Task Tracking:**
```
/memory-add-task --description "Fix authentication bug" --priority high
# ... work on task ...
/memory-complete-task --task_id abc123
```

**Work Logging:**
```
/memory-log --action "Completed API integration" --details "Added 3 new endpoints"
```

### Memory System Integration

The Memory System integrates with two context sources:

1. **Memory System** (`~/.claude/gz-observability-memory/`):
   - Persistent YAML files that survive across sessions
   - Manual checkpoints and task tracking
   - Long-term continuity

2. **Activity Logs** (Hooks at `.claude/logs/`):
   - Automatic activity capture via hooks
   - Real-time logging of all operations
   - 2-hour rolling window for recent activity

Both systems are used together by `/prime-agent` to provide complete context when starting a session.

### Memory System Requirements

- **PyYAML** (optional): Install with `pip install pyyaml` for proper YAML formatting
- Without PyYAML, the system falls back to JSON format with `.yaml` extension

---

## Beads: Graph-Based Knowledge System

Beads is a Gastown-inspired graph-based knowledge system that extends the memory system with a proper issue/bead graph featuring dependencies, workflows, and PostgreSQL sync.

### Key Features

- **Graph Relationships**: Issues (beads) can depend on each other, block each other, and form hierarchies
- **Molecule Workflows**: Define reusable workflow templates that instantiate as issue graphs
- **Hybrid Storage**: Project-level beads in `.beads/` + global beads in `~/.claude/beads/`
- **PostgreSQL Sync**: All bead events flow to The Well alongside Claude Code activity
- **CLI + Slash Commands**: Both `beads` CLI and `/bead-*` Claude Code commands

### Quick Start

```bash
# Initialize beads in your project
beads init --prefix myproj

# Create a task
beads create "Implement user authentication"

# Create a dependency
beads create "Setup database"
beads depend myproj-abc12 myproj-def34  # abc12 depends on def34

# See what's ready to work
beads ready

# In Claude Code
/bead-create "New task"
/bead-list --status open
/bead-ready
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `beads init` | Initialize beads database in current directory |
| `beads create <title>` | Create a new bead (issue) |
| `beads list` | List all beads (supports `--label`, `--global` flags) |
| `beads show <id>` | Show bead details (supports `--global` flag) |
| `beads update <id>` | Update bead fields |
| `beads close <id>` | Close a bead |
| `beads depend <id> <dep-id>` | Add dependency (id depends on dep-id) |
| `beads ready` | Show beads ready to work (not blocked) |
| `beads label <id> <label>` | Add a label to a bead |
| `beads migrate` | Migrate from legacy Memory System |

**Global vs Project Beads:**
- Project beads: stored in `.beads/` within your project directory
- Global beads: stored in `~/.claude/beads/` (used by migration, cross-project items)
- Use `--global` or `-g` flag with `list` and `show` to access global beads

### Migrating from Memory System

If you have existing data in the legacy Memory System (`~/.claude/gz-observability-memory/`), migrate it to Beads:

```bash
# Preview what would be migrated
beads migrate --dry-run

# Run full migration
beads migrate

# Migrate only work log entries
beads migrate --work-log-only

# Include JSONL hook logs
beads migrate --include-logs

# View migrated beads (stored in global database)
beads list --global --label migrated
```

**What gets migrated:**
- `work_log.yaml` → Beads with labels `[migrated, work-log]`
- `planned_tasks.yaml` → Beads with status mapped (pending→open, completed→done)
- `handoff_context.yaml` → Single bead with label `[handoff]`
- Project context files (`*_context.yaml`) → Beads with label `[context]`

Migration is **idempotent** - running it multiple times won't create duplicates.
Old YAML files remain in place for reference.

### Slash Commands

| Command | Description |
|---------|-------------|
| `/bead-create` | Create a new bead |
| `/bead-list` | List beads with filters |
| `/bead-show` | Show bead details |
| `/bead-update` | Update bead fields |
| `/bead-link` | Create dependency links |
| `/bead-ready` | Show ready beads |
| `/mol-pour` | Instantiate a molecule workflow |

### Molecule Workflows

Molecules are reusable workflow templates that create structured issue graphs:

```bash
# 1. Create a molecule template
beads create "API Implementation" --type molecule

# 2. Define steps in description:
## Step: design
Design the API schema
Tier: haiku

## Step: implement
Build the API
Needs: design
Tier: sonnet

## Step: test
Write tests
Needs: implement
Tier: haiku

# 3. Create a parent work item
beads create "Build User API" --type feature

# 4. Pour the molecule
/mol-pour <molecule-id> <parent-id>

# This creates 3 child beads with auto-wired dependencies:
# - "Design the API schema" (ready)
# - "Build the API" (blocked by design)
# - "Write tests" (blocked by implement)
```

### Bead Status Flow

```
OPEN → IN_PROGRESS → DONE
                  ↘
                   CLOSED
```

Ready beads are `OPEN` with all dependencies `DONE` or `CLOSED`.

### PostgreSQL Events

Bead events sync to The Well with these event types:

| Event Type | Description |
|------------|-------------|
| `BEAD_CREATED` | New bead created |
| `BEAD_UPDATED` | Bead fields changed |
| `BEAD_CLOSED` | Bead closed |
| `DEPENDENCY_ADDED` | Dependency link created |
| `MOLECULE_INSTANTIATED` | Molecule poured onto parent |

Query bead events in PostgreSQL:
```sql
SELECT * FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
WHERE EVENT_TYPE LIKE 'BEAD_%' OR EVENT_TYPE LIKE 'DEPENDENCY_%'
ORDER BY EVENT_AT DESC LIMIT 20;
```

---

## Persistent Memory System

The agent has persistent, semantically-indexed memory backed by Neon PostgreSQL + pgvector.
Unlike flat-file memory systems that require keyword matching, this memory is
searchable by **meaning** — the agent can recall decisions, people context,
technical patterns, and preferences even when the search terms don't match
the original words.

This is not a bolt-on feature. It is fundamental to how the agent operates.
The agent naturally commits decisions, preferences, and insights to memory as
it works. It recalls relevant context before starting tasks. It learns from
corrections and accumulates institutional knowledge across sessions.

### How It Works

```
You say: "Decided to use ROW_NUMBER for CertApp dedup because..."
   │
   ▼
┌──────────────────────────────────┐
│  Cortex LLM (mistral-large2)    │  ← Auto-extracts type, topics,
│  Metadata Extraction             │    people, summary, action_items
└──────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────┐
│  Embeddings (pgvector)               │  ← sentence-transformers
│  768-dim vector                  │    VECTOR(FLOAT, 768)
└──────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────┐
│  DW_DEV_STREAM.BRAIN.THOUGHTS   │  ← Stored with your USER_ID
└──────────────────────────────────┘

Later: "what was the dedup strategy for CertApp?"
   → VECTOR_COSINE_SIMILARITY() finds the thought above
```

### User Isolation

Every thought is scoped to a `USER_ID`. All queries filter by `WHERE USER_ID = %s`:
- `capture()`, `search()`, `recent()`, `stats()` — all user-scoped
- Table clustered by `(USER_ID, CREATED_AT)` for performance
- Views (`V_USER_STATS`, `V_USER_TOPICS`, `V_USER_PEOPLE`) group by `USER_ID`

**Your thoughts are never visible to other users.** Multiple developers can use the same table safely.

### Memory Operations

```bash
# Commit to memory
python3 scripts/open_brain.py --capture "Decided to use X because Y"

# Recall by meaning
python3 scripts/open_brain.py --search "CertApp dedup strategy" --limit 10

# Recent memories
python3 scripts/open_brain.py --recent --days 7
python3 scripts/open_brain.py --recent --days 30 --type decision

# Memory distribution
python3 scripts/open_brain.py --stats
python3 scripts/open_brain.py --stats --json
```

### Memory Slash Commands

| Command | Description |
|---------|-------------|
| `/brain-capture <text>` | Commit something to memory |
| `/brain-search <query>` | Search memory by meaning |
| `/brain-recent` | What have I been thinking about recently? |
| `/brain-stats` | How is my memory distributed? |
| `/brain-context` | Recall recent context — memories + pending tasks |

### What I Remember

Metadata is **auto-extracted** by Cortex LLM — no manual tagging needed:

| Type | Example |
|------|---------|
| `decision` | "Decided to use Postgres over Mongo because..." |
| `insight` | "The API has a 5MB payload limit" |
| `person_note` | "Sarah handles all GZ import approvals" |
| `meeting` | "Met Dave re: security signoff — approved Phase 2" |
| `idea` | "Could batch the sync daemon to reduce database costs" |
| `task` | "Need to add retry logic to the webhook handler" |
| `reflection` | "The ROW_NUMBER approach was cleaner than QUALIFY" |
| `preference` | "Chris prefers SQL-first, Python only for cross-DB" |
| `impression` | "That API is fragile — always validate responses" |
| `pattern` | "Fire-and-forget subprocess pattern prevents hook blocks" |
| `working_memory` | "Currently mid-NSSA Phase 3, 64 missing individuals to fix" |

### Schema Reference

```sql
-- Table: DW_DEV_STREAM.BRAIN.THOUGHTS
-- DDL: sql/BRAIN_SCHEMA.sql
-- Views: V_RECENT_THOUGHTS, V_USER_STATS, V_USER_TOPICS, V_USER_PEOPLE
```

---

## Memory Migration: Loading Historical Context

Migrates legacy Claude Code memory files and Beads JSONL into persistent memory with full vector embeddings and preserved timestamps. The script auto-discovers sources for any user — no hardcoded paths.

### What It Migrates

| Source | Discovery | Thought Type |
|--------|-----------|--------------|
| Memory `.md` files | `~/.claude/projects/*/memory/*.md` | `migration-memory` |
| Bead JSONL (significant) | `.beads/issues.jsonl` in dev dirs | `migration-bead` |
| Bead JSONL (rollup) | Grouped by project | `migration-bead-ledger` |

### Auto-Discovery

The script finds sources automatically based on the current user's environment:

- **Memory files**: Scans all `~/.claude/projects/<project-key>/memory/` directories
- **Bead files**: Searches `~/Documents/optivai/`, `~/Documents/`, `~/Development/`, `~/Projects/`, `~/Code/` (up to 4 levels deep)
- **Global beads**: Checks `~/.claude/beads/`

### Usage

```bash
# Preview what will be migrated (no writes)
python3 scripts/brain_transfer.py --dry-run --verbose

# Migrate in phases
python3 scripts/brain_transfer.py --memories-only
python3 scripts/brain_transfer.py --beads-only

# Migrate everything
python3 scripts/brain_transfer.py

# Custom source directories
python3 scripts/brain_transfer.py --memory-dir /path/to/memories
python3 scripts/brain_transfer.py --beads-dir /path/to/repos
```

### How It Works

1. **Memory Chunker** — Splits `.md` files on `## ` headers. Each section becomes one thought. Small files (< 3 sections) captured as a single thought.
2. **Bead Classifier** — Reads JSONL, classifies each bead:
   - **Individual thought**: Epics, memory-type beads, beads with descriptions > 100 chars
   - **Project ledger**: Remaining beads grouped into one summary thought per project
3. **Cortex Processing** — For each thought:
   - `COMPLETE('mistral-large2')` extracts metadata (type, topics, people, action_items, summary)
   - `EMBED_TEXT_768('sentence-transformers')` generates 768-dim vector embedding
4. **Direct INSERT** — Bypasses `capture()` to preserve original timestamps in `CREATED_AT`
5. **Dedup** — SHA-256 of first 500 chars prevents double-loading on re-run. User-scoped.

### Idempotency

Safe to re-run at any time. The script checks existing `migration-%` source rows (filtered by `USER_ID`) and skips anything already loaded. Running twice inserts 0 new rows.

### Verification

```bash
# Check total thought count and distribution
python3 scripts/open_brain.py --stats

# Semantic search across migrated content
python3 scripts/open_brain.py --search "who handles GZ imports"
python3 scripts/open_brain.py --search "CertApp dedup strategy"
```

---

## Session Continuity (Propulsion Principle)

The plugin implements the **Propulsion Principle** - a pattern from distributed agent architectures that maximizes productivity by detecting and surfacing pending work at session start.

### How It Works

1. **Before ending a session**: Run `/handoff [summary]` to save your current context
2. **When starting a new session**: Run `/prime-agent` which automatically:
   - Checks for pending tasks in `planned_tasks.yaml`
   - Checks for recent handoff context (< 24 hours old)
   - Displays work that needs attention

### The Propulsion Principle

> "If there's work on your hook, RUN IT."

Instead of asking "what would you like to do?", the agent:
- Detects pending/in-progress tasks
- Surfaces recent handoff context
- Prompts: "Ready to continue with [task]?"

This eliminates the "getting up to speed" time at session start.

### Handoff Command

```bash
# Before ending your session
/handoff "Implementing user authentication, left off at JWT validation"

# Creates ~/.claude/gz-observability-memory/handoff_context.yaml with:
# - Current state (project, focus, operation count)
# - What you were working on
# - Active and pending tasks
# - Recent work history
# - Suggested next steps
```

### Agent Identity

Each log entry now includes an `agent_id` field following the pattern `{project}/crew/{user}`. This enables:
- Per-agent analytics in PostgreSQL
- Multi-agent session tracking
- Work attribution across parallel agents

Override with environment variable: `CLAUDE_AGENT_ID=my-custom/agent/id`

---

## Ralph Loop (Iterative Development)

The plugin includes the **Ralph Wiggum Loop**, a self-referential development methodology for tasks requiring multiple iterations.

### How It Works

1. You provide a task prompt with optional completion criteria
2. Claude works on the task
3. When Claude tries to exit, the Stop hook intercepts
4. The same prompt is fed back
5. Claude sees its previous work in files and continues iterating
6. Loop exits when: completion promise is met, max iterations reached, or `/cancel-ralph` is run

### Usage

```bash
# Basic loop with iteration limit
/ralph-loop Build a REST API --max-iterations 20

# With completion promise (loop exits when promise is output)
/ralph-loop Fix all tests --completion-promise 'ALL TESTS PASS' --max-iterations 30

# TDD example
/ralph-loop "Fix all failing tests. Output <promise>ALL TESTS PASS</promise> when pytest returns 0 failures." --completion-promise "ALL TESTS PASS" --max-iterations 30
```

### Completion Promise

To signal task completion, output the promise in XML tags:

```
<promise>YOUR_COMPLETION_PHRASE</promise>
```

Only output a promise when the statement is TRUE.

### When to Use Ralph Loop

- Test-driven development (make tests pass)
- Iterative refinement (fix linting, refactor)
- Complex multi-step implementations
- Bug fix loops with clear success criteria
- Autonomous work sessions

### Monitoring & Control

```bash
# Check loop status
head -10 .claude/ralph-loop.local.md

# Cancel the loop
/cancel-ralph
```

### Installation

The Ralph Loop requires a Stop hook. Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/stop-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Copy the scripts:

```bash
cp scripts/stop-hook.sh ~/.claude/hooks/
cp scripts/setup-ralph-loop.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/stop-hook.sh ~/.claude/hooks/setup-ralph-loop.sh
```

---

## JIRA Integration

The plugin includes a `/jira` skill for managing JIRA tickets directly from Claude Code without switching to the web UI.

### JIRA Setup

#### Step 1: Run the Install Script

The install script creates the necessary directories and copies the JIRA client library:

```bash
cd optivai-claude-plugin
./scripts/install.sh
```

This installs:
- `~/.claude/lib/jira_client.py` - Python client library
- `~/.claude/config/jira-config.json` - Configuration template
- `~/.claude/commands/jira.md` - The `/jira` skill

#### Step 2: Configure Your Email

Edit `~/.claude/config/jira-config.json` and set your email address:

```json
{
  "instance_url": "https://YOUR_INSTANCE.atlassian.net",
  "email": "YOUR_EMAIL@example.com",
  "defaults": {
    "project_key": "PROJ",
    "project_name": "Your Project",
    "issue_type": "Task"
  },
  "users": {
    "username": {
      "account_id": "712020:your-account-id-here",
      "display_name": "Your Name"
    },
    "yemi": {
      "account_id": "712020:9be62922-a1eb-43d3-8e46-e8b119b5b65f",
      "display_name": "Yemi"
    }
  }
}
```

#### Step 3: Create Your API Token

1. Go to: https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a name (e.g., "Claude Code")
4. Copy the token and save it:

```bash
echo 'YOUR_JIRA_API_TOKEN' > ~/.claude/secrets/jira-token
chmod 600 ~/.claude/secrets/jira-token
```

#### Step 4: Test the Connection

In Claude Code:
```
/jira test
```

Expected output:
```
Connected to JIRA as: Your Name
Default Project: PROJ (Your Project)
Instance: https://YOUR_INSTANCE.atlassian.net
```

### JIRA Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `/jira test` | Test connection | `/jira test` |
| `/jira create` | Create ticket | `/jira create "Fix ETL bug" --assignee chandler` |
| `/jira view` | View ticket | `/jira view DA-123` |
| `/jira assign` | Assign ticket | `/jira assign DA-123 marshall` |
| `/jira comment` | Add comment | `/jira comment DA-123 "Fixed in commit abc123"` |
| `/jira transition` | Change status | `/jira transition DA-123 "Done"` |
| `/jira search` | Search with JQL | `/jira search "project = DA AND status != Done"` |

### Create Options

| Option | Description | Example |
|--------|-------------|---------|
| `--assignee` | Assign to user | `--assignee chandler` |
| `--watchers` | Add watchers | `--watchers marshall,chris` |
| `--description` | Issue description | `--description "Details here"` |
| `--priority` | Priority level | `--priority High` |
| `--labels` | Labels | `--labels bug,urgent` |

### Common JQL Queries

| Query | Description |
|-------|-------------|
| `project = DA` | All DA tickets |
| `project = DA AND status != Done` | Open DA tickets |
| `assignee = currentUser()` | My tickets |
| `created >= -7d` | Created in last 7 days |
| `labels = claude-code` | Labeled claude-code |

### Adding Team Members

To add a new team member to the config:

1. Find their account ID:
```bash
curl -s "https://YOUR_INSTANCE.atlassian.net/rest/api/3/user/search?query=firstname" \
  -u "your.email@example.com:YOUR_TOKEN" | python3 -m json.tool
```

2. Add to `~/.claude/config/jira-config.json`:
```json
"firstname": {
  "account_id": "712020:xxxxx-xxxx-xxxx",
  "display_name": "First Last"
}
```

---

## Superpowers Skills

The plugin includes **Superpowers** - a collection of advanced workflow skills originally from the official Claude Code plugins. These provide structured methodologies for common development tasks.

### Available Superpowers

| Skill | Purpose | When to Use |
|-------|---------|-------------|
| `/superpowers:brainstorming` | Structured ideation and requirements gathering | Before starting any creative work or new feature |
| `/superpowers:writing-plans` | Create detailed implementation plans | When you have specs but need to plan execution |
| `/superpowers:executing-plans` | Execute plans with review checkpoints | When implementing a multi-step plan |
| `/superpowers:test-driven-development` | TDD workflow enforcement | When implementing features or bugfixes |
| `/superpowers:systematic-debugging` | Structured bug investigation | When encountering bugs or test failures |
| `/superpowers:verification-before-completion` | Pre-commit verification | Before claiming work is complete |
| `/superpowers:requesting-code-review` | Request structured code review | When completing tasks or major features |
| `/superpowers:receiving-code-review` | Process code review feedback | When receiving review comments |
| `/superpowers:finishing-a-development-branch` | Branch completion workflow | When implementation is complete and tests pass |
| `/superpowers:using-git-worktrees` | Git worktree management | When starting isolated feature work |
| `/superpowers:dispatching-parallel-agents` | Parallel task execution | When facing 2+ independent tasks |
| `/superpowers:subagent-driven-development` | Subagent coordination | When executing plans with independent tasks |
| `/superpowers:writing-skills` | Create new skills | When creating or editing skills |
| `/superpowers:using-superpowers` | Introduction to skills system | Reference for how skills work |

### How Superpowers Work

Superpowers are **rigid process skills** - they enforce specific workflows:

1. **Invoke the skill** before starting the related task
2. **Follow the checklist** exactly as specified
3. **Don't skip steps** - the discipline is the value

Example:
```bash
# Before writing any implementation code
/superpowers:test-driven-development

# Before claiming "it's fixed"
/superpowers:verification-before-completion
```

### Key Superpowers Principles

**Process over shortcuts**:
- "This is just a simple fix" → Still use TDD
- "I know what to do" → Still use brainstorming for creative work
- "Tests pass, we're done" → Still verify before claiming completion

**When in doubt, invoke**:
- Even 1% chance a skill might apply → invoke it
- If it turns out to be wrong → fine, but check first

### Installing Superpowers

Superpowers are automatically installed with the main install script:

```bash
./scripts/install.sh
```

Or manually sync:

```bash
./scripts/update-skills.sh
```

This copies all superpowers to `~/.claude/commands/superpowers/`.

---

## Keeping Skills Updated (Auto-Sync)

When you pull updates to this plugin, new skills and improvements need to be deployed to your `~/.claude/` directory.

### Automatic Sync (Recommended)

The install script sets up a **git post-merge hook** that automatically syncs skills after every `git pull`:

```bash
# After running install.sh once, this happens automatically:
git pull
# Output:
# 🔄 Auto-syncing Claude Code skills...
# Syncing skills to ~/.claude/commands/...
# Syncing agents to ~/.claude/agents/...
# Syncing client libraries to ~/.claude/lib/...
# ✓ Synced 27 skills and 20 agents
```

### Manual Sync

If the auto-sync isn't working, you can manually sync:

```bash
cd optivai-claude-plugin
./scripts/update-skills.sh
```

### What Gets Synced

| Source | Destination | Contents |
|--------|-------------|----------|
| `.claude/commands/*.md` | `~/.claude/commands/` | Slash command skills |
| `agents/*.md` | `~/.claude/agents/` | Agent templates |
| `claude_lib/*.py` | `~/.claude/lib/` | Client libraries (JIRA, etc.) |

### Reinstalling

If you need to fully reinstall:

```bash
cd optivai-claude-plugin
git pull
./scripts/install.sh
```

This is safe to run multiple times - it won't overwrite your personal configs (credentials, tokens).

---

## Context Rot Detection

The `/prime-agent` command includes a **context rot detection** mechanism to identify when LLM context window management is causing information loss.

### How It Works

1. At session start, the agent generates a random 8-character alphanumeric identifier
2. The user is assigned a name in format `User-XXXXXXXX` (e.g., `User-k7m9p2x4`)
3. The agent must address the user by this name throughout the entire conversation
4. If the agent forgets or changes this name mid-conversation, it indicates **context rot**

### What is Context Rot?

**Context rot** occurs when:
- The LLM's context window fills up and older information is summarized or dropped
- Important details from earlier in the conversation are lost
- The model "forgets" things it was told at the start of the session

### Why This Matters

In long coding sessions, context rot can cause:
- Forgetting project constraints or coding standards
- Re-introducing bugs that were already discussed
- Losing track of the overall task architecture
- Inconsistent behavior as the session progresses

### Detecting Context Rot

If the agent:
- Stops using your assigned username → **Context rot detected**
- Uses a different username than assigned → **Context rot detected**
- Asks for your name again → **Context rot detected**

When you detect context rot, consider:
1. Creating a new checkpoint: `/memory-checkpoint`
2. Starting a fresh session: `/new-session`
3. Re-priming with: `/prime-agent`

### Example

```
# At session start after /prime-agent:
"Hello User-k7m9p2x4, I've reviewed the codebase..."

# If later in the session you see:
"Hello, I'll help you with that..."  # ← Missing username = context rot!
```

---

## Agents (Subagents)

This plugin includes specialized AI agent templates that can be invoked as "subagents" within Claude Code.

### What are Subagents?

In Claude Code, **subagents** are specialized AI personas that you can launch to handle specific types of tasks. When you invoke a subagent:

1. Claude spawns a separate task with the agent's specialized instructions
2. The subagent works autonomously with its own context and expertise
3. Results are returned to the main conversation

Subagents are useful for:
- **Specialized expertise** - Data architecture, code review, technical writing
- **Complex multi-step tasks** - Let the subagent handle intricate work autonomously
- **Parallel processing** - Launch multiple subagents to work on different aspects simultaneously

### How to Invoke Subagents

**Method 1: Ask Claude to use an agent**
```
Use the data-architect agent to design the schema for this feature
```

**Method 2: Reference the agent file**
```
Read agents/data-architect.md and apply that persona to design this schema
```

**Method 3: Via Task tool (automatic)**
Claude will automatically use the Task tool to spawn subagents when appropriate based on the agent descriptions.

### Available Agents

| Agent | Purpose | Use When |
|-------|---------|----------|
| `data-architect` | Enterprise data architecture, schema design, data modeling | Designing PostgreSQL schemas, data models, integration patterns |
| `data-quality-analyst` | Data validation, quality assessment, anomaly detection | Analyzing data quality issues, creating validation rules |
| `data-quality-manager` | Data governance, quality frameworks, standards | Establishing data quality policies and procedures |
| `postgresql-specialist` | PostgreSQL platform expertise, pgvector, optimization | PostgreSQL-specific features, performance tuning, cost optimization |
| `sql-developer` | SQL development for PostgreSQL | Complex queries, stored procedures, optimization |
| `etl-pipeline-developer` | Python/SQL data pipelines, ETL/ELT development | Building data pipelines, sync daemons, incremental loads |
| `solution-architect` | End-to-end solution design, cloud architecture, integration | Designing complex systems, multi-system integrations |
| `solution-architect-planner` | Architecture planning, phased delivery, roadmaps | Planning implementation phases, creating technical roadmaps |
| `implementation-developer` | Production-ready code, complete implementations | Writing complete, tested code without placeholders |
| `code-quality-reviewer` | Code review, security analysis, best practices | Reviewing code before commits, finding bugs and vulnerabilities |
| `devops-deployment-specialist` | CI/CD, infrastructure, deployment automation | Setting up pipelines, containerization, deployment scripts |
| `program-manager` | Project planning, stakeholder management, delivery | Managing complex initiatives, coordinating workstreams |
| `technical-writer` | Documentation, guides, technical content | Creating user guides, API documentation, runbooks |
| `docs-scraper` | Web content extraction, documentation gathering | Scraping technical documentation, research |
| `prompt-engineer-optimizer` | Prompt optimization, AI interaction design | Improving prompts, designing AI workflows |
| `strategic-planning-manager` | Strategic initiatives, planning frameworks | High-level strategic planning, initiative design |
| `ui-ux-frontend-engineer` | Frontend development, UI implementation | Building user interfaces (if needed) |
| `user-research-expert` | User research, persona development | Understanding user needs and behaviors |
| `ux-ui-design-manager` | Design systems, UX strategy | UI/UX design direction and standards |

### Installing Agents

Copy the agent templates to your Claude Code directory:

```bash
# Create agents directory if it doesn't exist
mkdir -p ~/.claude/agents

# Copy all agent files
cp agents/*.md ~/.claude/agents/
```

### Recommended Agents

For the tech stack (PostgreSQL, Python), the most relevant agents are:

| Priority | Agent | Use Cases |
|----------|-------|---------------------|
| ⭐ High | `postgresql-specialist` | PostgreSQL indexes/partitions, query optimization, pgvector |
| ⭐ High | `sql-developer` | Complex SQL for PostgreSQL, query optimization |
| ⭐ High | `etl-pipeline-developer` | Python ETL scripts, sync daemons, data pipeline development |
| ⭐ High | `data-architect` | Schema design, data modeling, integration patterns |
| ⭐ High | `implementation-developer` | Production-ready Python scripts |
| ⭐ High | `data-quality-analyst` | Data validation rules, pipeline QA, anomaly detection |
| ⭐ High | `code-quality-reviewer` | Review Python scripts, SQL code, hook implementations |
| Medium | `solution-architect` | Multi-system integration design |
| Medium | `devops-deployment-specialist` | Deployment automation, scheduling sync daemons |
| Medium | `technical-writer` | Documentation for pipelines, runbooks |
| Lower | Others | General-purpose, less specific to data engineering |

### Creating Custom Agents

To create a new specialized agent:

1. Create a markdown file in `agents/` with YAML frontmatter:

```markdown
---
name: my-custom-agent
description: Description of when to use this agent
model: opus
color: blue
---

# My Custom Agent

## Role Definition
You are now operating as a **[Role Name]**. Your expertise includes:
- Capability 1
- Capability 2

## Core Competencies
[Define what this agent excels at]

## Methodology
[Define how the agent approaches problems]

## Deliverable Standards
[Define output quality expectations]
```

2. Copy to your Claude directory: `cp agents/my-custom-agent.md ~/.claude/agents/`

---

## The Well Event Schema

Events synced to PostgreSQL follow this schema:

| Column | Type | Description |
|--------|------|-------------|
| `EVENT_ID` | VARCHAR | Unique event identifier |
| `TENANT_ID` | VARCHAR | Tenant identifier (default: `CLAUDE_CODE`) |
| `SOURCE_SYSTEM` | VARCHAR | Source system identifier |
| `EVENT_TYPE` | VARCHAR | Event type in format `ACTOR.CATEGORY.OPERATION` |
| `EVENT_AT` | TIMESTAMP_TZ | When the event occurred |
| `ACTOR_ID` | VARCHAR | Who performed the action |
| `ACTOR_TYPE` | VARCHAR | Type of actor (`BOT`, `USER`) |
| `SUBJECT_ID` | VARCHAR | What was affected (project name) |
| `SUBJECT_TYPE` | VARCHAR | Type of subject (`PROJECT`) |
| `METADATA` | VARIANT | JSON with full event details |
| `INGESTED_AT` | TIMESTAMP_TZ | When synced to PostgreSQL |
| `EVENT_NK_HASH` | VARCHAR | Natural key hash for deduplication |

### Event Types

| Event Type | Description |
|------------|-------------|
| `USER.PROMPT.USER_PROMPT` | User submitted a prompt |
| `BOT.TOOL.READ` | Claude read a file |
| `BOT.TOOL.WRITE` | Claude wrote a file |
| `BOT.TOOL.EDIT` | Claude edited a file |
| `BOT.TOOL.BASH` | Claude executed a command |
| `BOT.TOOL.GLOB` | Claude searched for files |
| `BOT.TOOL.GREP` | Claude searched file contents |
| `BOT.TOOL.TASK` | Claude launched a subagent |
| `BOT.TOOL.TODO_WRITE` | Claude updated task list |
| `SESSION.SUMMARY` | End-of-session token usage and cost summary |

---

## Querying Your Activity

There are **two tables** you can query for Claude Code activity:

| Table | Use Case | Latency |
|-------|----------|---------|
| `DW_DEV_STREAM.LANDING.RAW_EVENTS` | Real-time debugging, immediate verification | ~60 seconds |
| `DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM` | Analytics, reporting, unified view | ~15 minutes |

### Activity Stream Queries (Recommended for Analytics)

**Your recent activity (last 24 hours):**
```sql
SELECT
    EVENT_AT,
    EVENT_TYPE,
    ACTOR_ID AS USER,
    SUBJECT_ID AS PROJECT,
    METADATA:operation::STRING AS OPERATION,
    METADATA:prompt::STRING AS PROMPT
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(hour, -24, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC
LIMIT 100;
```

**Filter by specific user:**
```sql
SELECT
    EVENT_AT,
    EVENT_TYPE,
    SUBJECT_ID AS PROJECT,
    METADATA:operation::STRING AS OPERATION,
    LEFT(METADATA:prompt::STRING, 100) AS PROMPT_PREVIEW
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND ACTOR_ID LIKE '%your_username%'  -- Replace with your username
  AND EVENT_AT > DATEADD(day, -7, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC;
```

**Activity summary by project:**
```sql
SELECT
    SUBJECT_ID AS PROJECT,
    COUNT(*) AS TOTAL_EVENTS,
    COUNT(CASE WHEN EVENT_TYPE LIKE '%READ%' THEN 1 END) AS READS,
    COUNT(CASE WHEN EVENT_TYPE LIKE '%WRITE%' OR EVENT_TYPE LIKE '%EDIT%' THEN 1 END) AS WRITES,
    COUNT(CASE WHEN EVENT_TYPE LIKE '%BASH%' THEN 1 END) AS BASH_COMMANDS,
    COUNT(CASE WHEN EVENT_TYPE LIKE '%PROMPT%' THEN 1 END) AS USER_PROMPTS,
    MIN(EVENT_AT) AS FIRST_ACTIVITY,
    MAX(EVENT_AT) AS LAST_ACTIVITY
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY TOTAL_EVENTS DESC;
```

**Daily activity trends:**
```sql
SELECT
    DATE(EVENT_AT) AS ACTIVITY_DATE,
    COUNT(*) AS TOTAL_EVENTS,
    COUNT(DISTINCT SUBJECT_ID) AS PROJECTS_WORKED,
    COUNT(DISTINCT ACTOR_ID) AS UNIQUE_USERS
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 1 DESC;
```

**Most common operations:**
```sql
SELECT
    METADATA:operation::STRING AS OPERATION,
    COUNT(*) AS COUNT,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS PERCENTAGE
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -7, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 2 DESC;
```

**Search prompts containing specific text:**
```sql
SELECT
    EVENT_AT,
    ACTOR_ID AS USER,
    SUBJECT_ID AS PROJECT,
    METADATA:prompt::STRING AS PROMPT
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND LOWER(METADATA:prompt::STRING) LIKE '%search term%'
  AND EVENT_AT > DATEADD(day, -30, CURRENT_TIMESTAMP())
ORDER BY EVENT_AT DESC;
```

### RAW_EVENTS Queries (Real-Time)

Use these for immediate verification that logging is working:

**Check if your latest activity is synced:**
```sql
SELECT
    EVENT_AT,
    EVENT_TYPE,
    ACTOR_ID,
    SUBJECT_ID AS PROJECT
FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
ORDER BY EVENT_AT DESC
LIMIT 10;
```

**Today's activity count:**
```sql
SELECT
    COUNT(*) AS events_today,
    MIN(EVENT_AT) AS first_event,
    MAX(EVENT_AT) AS last_event
FROM DW_DEV_STREAM.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND DATE(EVENT_AT) = CURRENT_DATE();
```

### Activity Stream Schema

The Activity Stream includes these columns for Claude Code events:

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `EVENT_ID` | VARCHAR | Unique event ID | `cc-1736700146-3a4a3eca` |
| `TENANT_ID` | VARCHAR | Tenant identifier | `CLAUDE_CODE` |
| `SOURCE_SYSTEM` | VARCHAR | Always `CLAUDE_CODE` | `CLAUDE_CODE` |
| `EVENT_TYPE` | VARCHAR | Operation type | `BOT.BASH`, `USER.PROMPT.PROMPT` |
| `EVENT_AT` | TIMESTAMP_LTZ | When event occurred | `2026-01-12 14:02:18` |
| `ACTOR_ID` | VARCHAR | User identifier | `claude-code-christopherhughesgz` |
| `ACTOR_TYPE` | VARCHAR | Actor type | `BOT` |
| `SUBJECT_ID` | VARCHAR | Project name | `optivai-claude-plugin` |
| `SUBJECT_TYPE` | VARCHAR | Subject type | `PROJECT` |
| `METADATA` | VARIANT | JSON with details | See below |
| `INGESTED_AT` | TIMESTAMP_LTZ | When synced | `2026-01-12 14:02:47` |
| `EVENT_NK_HASH` | VARCHAR | Deduplication hash | `a1b2c3d4...` |

**METADATA JSON structure:**
```json
{
  "operation": "bash",
  "prompt": "Check sync status",
  "session_id": "session-20260112-140218-abc123",
  "agent_id": "optivai-claude-plugin/crew/christopherhughesgz",
  "user": "christopherhughesgz",
  "project": "optivai-claude-plugin",
  "cwd": "/Users/christopherhughesgz/project",
  "timestamp": "2026-01-12T14:02:18+00:00",
  "git_repo": "feedbackloopai/optivai-claude-plugin",
  "git_org": "feedbackloopai",
  "git_repo_name": "optivai-claude-plugin",
  "provider": {
    "type": "teams",
    "model": "claude-opus-4-6",
    "organization": "FeedbackLoopAI",
    "user_email": "user@feedbackloopai.com"
  },
  "details": {
    "tool_name": "Bash",
    "command": "python3 sync.py --status"
  }
}
```

### Accessing METADATA Fields

Use PostgreSQL's JSON operators to extract METADATA fields:

```sql
SELECT
    METADATA:operation::STRING AS operation,
    METADATA:prompt::STRING AS prompt,
    METADATA:user::STRING AS user,
    METADATA:session_id::STRING AS session_id,
    METADATA:git_repo::STRING AS git_repo,
    METADATA:git_org::STRING AS git_org,
    METADATA:git_repo_name::STRING AS git_repo_name,
    COALESCE(METADATA:provider.model::STRING, METADATA:bedrock.model::STRING) AS model,
    METADATA:details:tool_name::STRING AS tool_name,
    METADATA:details:command::STRING AS command
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
LIMIT 5;
```

### Git Repository Attribution

The plugin automatically detects the git repository by parsing `git remote get-url origin`. This provides accurate repository attribution regardless of where Claude Code was launched from.

**Git fields in METADATA:**

| Field | Description | Example |
|-------|-------------|---------|
| `git_repo` | Full repository path (org/repo) | `feedbackloopai/optivai-claude-plugin` |
| `git_org` | Organization or owner | `feedbackloopai` |
| `git_repo_name` | Repository name only | `optivai-claude-plugin` |

**Query activity by git repository:**
```sql
SELECT
    METADATA:git_repo_name::STRING AS repo,
    COUNT(*) AS operations,
    COUNT(DISTINCT DATE(EVENT_AT)) AS days_active
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND METADATA:git_repo_name IS NOT NULL
  AND EVENT_AT > DATEADD(day, -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY operations DESC;
```

**Activity by organization:**
```sql
SELECT
    METADATA:git_org::STRING AS organization,
    COUNT(*) AS total_operations,
    COUNT(DISTINCT METADATA:git_repo_name::STRING) AS repos_worked
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND METADATA:git_org IS NOT NULL
  AND EVENT_AT > DATEADD(day, -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY total_operations DESC;
```

**Why Git Repo Attribution Matters:**

- **Accurate Project Tracking**: The launch directory (`SUBJECT_ID`) may not match the actual repository being worked on
- **Organization Analytics**: See work distribution across GitHub organizations
- **Repository-Level Costs**: Attribute costs to specific repositories, not just directories
- **Cross-Repo Work Detection**: Identify when sessions touch multiple repositories

---

## Cost Estimation & Token Analytics

The plugin provides two complementary approaches to cost estimation:

### Approach 1: Token-Based Session Summaries (Current)

The `session_summary.py` Stop hook parses Claude Code transcript JSONL files at session end to extract actual token usage. This provides precise, per-session token counts and API-equivalent cost estimates.

**Token Types Tracked:**

| Token Type | Description | Cost Factor |
|------------|-------------|-------------|
| `input_tokens` | Tokens sent to the model | Base input rate |
| `output_tokens` | Tokens generated by the model | Base output rate |
| `cache_creation_input_tokens` | Tokens written to prompt cache | 1.25x input rate |
| `cache_read_input_tokens` | Tokens read from prompt cache | 0.10x input rate |

**Pricing by Model Family (per 1M tokens):**

| Model | Input | Output | Cache Write | Cache Read |
|-------|-------|--------|-------------|------------|
| Claude Opus 4.x | $15.00 | $75.00 | $18.75 | $1.50 |
| Claude Sonnet 4.x | $3.00 | $15.00 | $3.75 | $0.30 |
| Claude Haiku 4.x | $0.80 | $4.00 | $1.00 | $0.08 |

**Note on Teams/Enterprise billing:** For Teams, Enterprise, Pro, and Max subscriptions, actual billing is subscription-based (fixed cost). The cost estimates in these views represent **API-equivalent cost** - what the same usage would cost at direct API rates. This is useful for understanding usage intensity, not actual billing.

### Token Usage Views

Three views in `DW_DEV_REPORT.RPT` provide token analytics from `SESSION.SUMMARY` events:

| View | Purpose |
|------|---------|
| `VW_CLAUDE_CODE_SESSION_TOKENS` | Per-session token breakdown with full detail |
| `VW_CLAUDE_CODE_DEVELOPER_USAGE` | Daily developer rollup (sessions, tokens, cost) |
| `VW_CLAUDE_CODE_MONTHLY_REPORT` | Monthly aggregation for leadership reporting |

**Per-Session Token Detail:**
```sql
SELECT
    SESSION_ID,
    USER_NAME,
    MODEL,
    INPUT_TOKENS,
    OUTPUT_TOKENS,
    CACHE_WRITE_TOKENS,
    CACHE_READ_TOKENS,
    TOTAL_TOKENS,
    ESTIMATED_COST_USD AS API_EQUIV_COST_USD,
    SESSION_DURATION_MINUTES
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_SESSION_TOKENS
WHERE SESSION_DATE >= DATEADD(day, -7, CURRENT_DATE())
ORDER BY SESSION_END_AT DESC;
```

**Daily Developer Usage:**
```sql
SELECT
    USAGE_DATE,
    USER_NAME,
    SESSION_COUNT,
    TOTAL_TOKENS,
    TOTAL_ESTIMATED_COST_USD,
    TOTAL_ACTIVE_HOURS,
    AVG_TOKENS_PER_SESSION
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_DEVELOPER_USAGE
ORDER BY USAGE_DATE DESC;
```

**Monthly Report:**
```sql
SELECT
    REPORT_MONTH,
    USER_NAME,
    SESSION_COUNT,
    ACTIVE_DAYS,
    TOTAL_TOKENS,
    API_EQUIV_COST_USD,
    TOTAL_ACTIVE_HOURS,
    AVG_SESSIONS_PER_DAY
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_MONTHLY_REPORT
ORDER BY REPORT_MONTH DESC;
```

### Approach 2: Operation-Based Cost Estimation (Legacy/Bedrock)

For Bedrock deployments, operation-count-based cost estimation validated against AWS CloudWatch billing data is also available.

| View | Purpose |
|------|---------|
| `VW_CLAUDE_CODE_COST_ESTIMATE` | Daily cost estimates by project |
| `VW_CLAUDE_CODE_COST_BY_PROJECT` | Project rollup with consolidated names |
| `VW_CLAUDE_CODE_MONTHLY_COST` | Monthly summaries for budgeting |

### Project Consolidation

The `VW_CLAUDE_CODE_COST_BY_PROJECT` view consolidates similar project names:

| Raw Project Names | Consolidated As |
|-------------------|-----------------|
| `ngaus`, `ngaus2`, `ngaus3`, `ngaus_reload` | `NGAUS` |
| `hmgs`, `HMGS3`, `HMGS4` | `HMGS` |
| `asmbs`, `ASMBS` | `ASMBS` |
| `reporting1`, `reporting3` | `Reporting` |
| `DataFactoryAssemblyLine1`, `ms-data-factory` | `DataFactory` |

### Validating Cost Estimates

To validate the formula against actual CloudWatch billing:

```bash
# Get CloudWatch metrics for comparison
aws cloudwatch get-metric-statistics \
  --namespace AWS/Bedrock \
  --metric-name Invocations \
  --start-time 2026-01-17T00:00:00Z \
  --end-time 2026-01-25T00:00:00Z \
  --period 86400 \
  --statistics Sum \
  --region us-east-1

# Compare Invocations with Activity Stream operations
# Formula: EST_COST = INVOCATIONS × $0.063
```

### Derived Project Context

**Problem**: The `SUBJECT_ID` field is just the directory where Claude Code was launched, but actual work may span multiple projects.

**Solution**: Views that extract project context from file paths using multiple heuristics:

1. **`/projects/PROJECT_NAME/`** pattern (highest priority)
2. **Repository patterns** (DataFactoryAssemblyLine1, optivai-claude-plugin, etc.)
3. **DDL folder** for governance work
4. **Fall back** to launch directory

#### Derived Project Views

| View | Purpose |
|------|---------|
| `VW_CLAUDE_CODE_DERIVED_PROJECT` | Event-level view with derived project context |
| `VW_CLAUDE_CODE_PROJECT_ACTIVITY` | Daily activity aggregated by derived project |
| `VW_CLAUDE_CODE_PROJECT_SUMMARY_DERIVED` | Project summary using derived context |
| `VW_CLAUDE_CODE_CROSS_PROJECT_WORK` | Sessions that worked across multiple projects |

#### Example: Project Activity by Derived Context

```sql
-- See actual project work (not just launch directory)
SELECT
    DERIVED_PROJECT,
    TOTAL_OPERATIONS,
    LAUNCH_DIRS_USED,  -- Shows work from multiple launch directories
    UNIQUE_FILES,
    EST_COST_USD
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_PROJECT_SUMMARY_DERIVED
ORDER BY TOTAL_OPERATIONS DESC;
```

**Sample Output:**
```
DERIVED_PROJECT         OPS    LAUNCH_DIRS  FILES    COST
DataFactory           1,835          13       412    $115.61
HMGS                    853           8       201     $53.74
NGAUS_FINAL             659          10       187     $41.52
DDL-Governance          412          10        89     $25.96
```

Note how "DataFactory" work was done from 13 different launch directories but is correctly attributed to a single project.

#### Cross-Project Work Detection

```sql
-- Find sessions that worked on multiple projects
SELECT
    SESSION_DATE,
    SESSION_ID,
    LAUNCH_DIR,
    PROJECTS_TOUCHED,
    PRIMARY_PROJECT,
    PROJECTS_LIST
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_CROSS_PROJECT_WORK
WHERE PROJECTS_TOUCHED > 1
ORDER BY SESSION_DATE DESC;
```

### Weighted Cost Attribution

**Problem**: Only file operations (~25% of all ops) have file paths for project derivation. Bash, Grep, Glob operations (~75%) can only be attributed to the launch directory.

**Solution**: `VW_CLAUDE_CODE_WEIGHTED_PROJECT_COST` allocates non-file operations based on the file operation distribution from each launch directory.

**Example**: If launch dir "hmgs" has file ops distributed as:
- 30% HMGS, 25% DDL-Governance, 18% HMGS3, 14% DataFactory...

Then non-file ops from "hmgs" are allocated using those same weights.

```sql
-- Full cost attribution with weighted allocation
SELECT
    DERIVED_PROJECT,
    FILE_OPS,           -- Direct file operations
    ALLOCATED_OPS,      -- Non-file ops allocated by weight
    TOTAL_OPS,          -- FILE_OPS + ALLOCATED_OPS
    EST_COST_USD,
    PCT_OF_TOTAL
FROM DW_DEV_REPORT.RPT.VW_CLAUDE_CODE_WEIGHTED_PROJECT_COST
ORDER BY TOTAL_OPS DESC;
```

**Sample Output:**
```
PROJECT           FILE_OPS  ALLOCATED   TOTAL_OPS     COST      %
DataFactory          2,330      6,837       9,167   $577.52  23.2%
ASMBS                1,045      5,269       6,314   $397.78  15.9%
NGAUS_FINAL            874      3,032       3,906   $246.08   9.9%
NGAUS                  783      2,741       3,524   $222.01   8.9%
HMGS                   854      2,158       3,012   $189.76   7.6%
```

### Additional Claude Code Views

Beyond cost estimation, these views provide operational analytics:

| View | Purpose |
|------|---------|
| `VW_CLAUDE_CODE_SESSION_TOKENS` | Per-session token breakdown (from SESSION.SUMMARY events) |
| `VW_CLAUDE_CODE_DEVELOPER_USAGE` | Daily developer rollup with token totals and cost |
| `VW_CLAUDE_CODE_MONTHLY_REPORT` | Monthly aggregation for leadership reporting |
| `VW_CLAUDE_CODE_TOOL_ACTIVITY` | Detailed tool-level activity with file paths, commands, git repo |
| `VW_CLAUDE_CODE_SESSIONS` | Session-level aggregations by project, git repo, and date |
| `VW_CLAUDE_CODE_DAILY_SUMMARY` | Daily activity summary across all projects |
| `VW_CLAUDE_CODE_PROJECT_SUMMARY` | Project-level rollup of all activity (uses launch dir) |
| `VW_CLAUDE_CODE_BY_USER_REPO` | Activity aggregated by user and git repository |
| `VW_CLAUDE_CODE_DAILY_BY_USER_REPO` | Daily activity by user and git repository |

---

## Configuration Reference

### Logging Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `logging.enabled` | Master switch for logging | `true` |
| `logging.log_dir` | Directory for local logs | `~/.claude/logs` |
| `logging.log_tool_operations` | Log tool calls | `true` |
| `logging.log_user_prompts` | Log user prompts | `true` |
| `logging.max_prompt_length` | Max prompt length before truncation | `500` |
| `logging.session_tracking` | Track session metadata | `true` |

### PostgreSQL Settings

| Setting | Description |
|---------|-------------|
| `postgresql.enabled` | Enable PostgreSQL sync |
| `postgresql.account` | Account identifier (e.g., `XYZ12345.us-east-1`) |
| `postgresql.warehouse` | Compute warehouse |
| `postgresql.role` | Role for connection |
| `postgresql.target_table` | Full table path |
| `postgresql.tenant_id` | Tenant ID in The Well |
| `postgresql.source_system` | Source system identifier |
| `postgresql.auth.user` | PostgreSQL username |
| `postgresql.auth.private_key_path` | Path to private key file |

### Sync Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `sync.batch_size` | Events per sync batch | `100` |
| `sync.flush_interval_seconds` | Seconds between syncs | `60` |
| `sync.retry_attempts` | Retries on failure | `3` |
| `sync.retry_delay_seconds` | Delay between retries | `5` |

---

## Troubleshooting

### Logs not appearing locally

1. Check that hooks are configured in `~/.claude/settings.json`
2. Verify hook scripts exist: `ls ~/.claude/hooks/*.py`
3. Check for errors: `cat ~/.claude/logs/hook_errors.log`

### Logs not syncing to PostgreSQL

1. Verify `postgresql.enabled: true` in config
2. Test connection: run `/db-connect` in Claude Code
3. Check sync status: `python3 scripts/pg_sync.py --status`
4. View daemon logs: `cat ~/.claude/logs/sync_daemon_stdout.log`
5. Try manual sync: `python3 ~/.claude/hooks/pg_sync.py --once --verbose`

### macOS "Operation not permitted" errors (Full Disk Access)

If the sync daemon shows errors like `[Errno 1] Operation not permitted` when reading log files, this is a **macOS Full Disk Access** permission issue. The launchd daemon runs in a sandboxed context that can't read files in `~/Documents` without explicit permission.

**Solution 1: Grant Full Disk Access to Python (Recommended)**

1. Open **System Preferences** → **Privacy & Security** → **Full Disk Access**
2. Click the **+** button
3. Navigate to `/usr/bin/python3` (or your Python installation)
4. Add Python to the list and enable the checkbox
5. Restart the daemon: `launchctl stop com.feedbackloopai.claude-pg-sync && launchctl start com.feedbackloopai.claude-pg-sync`

**Solution 2: Use Manual Sync**

Run syncs manually from your terminal (which has FDA by default):

```bash
# One-time sync
python3 ~/.claude/hooks/pg_sync.py --once --verbose

# Set up cron job instead of launchd
crontab -e
# Add: */5 * * * * python3 ~/.claude/hooks/pg_sync.py --once
```

**Solution 3: Use /sync-now Command**

In Claude Code, manually trigger sync:
```
/sync-now
```

**Verifying the fix:**
```bash
# Check daemon logs after granting FDA
tail -f ~/.claude/logs/sync_daemon_stdout.log
# Should show "Inserted X events" instead of "Operation not permitted"
```

### Key-pair authentication errors

1. Verify key file exists and has correct permissions:
   ```bash
   ls -la ~/.postgresql/your_key.p8
   chmod 600 ~/.postgresql/your_key.p8
   ```
2. Verify public key is registered in PostgreSQL:
   ```sql
   DESC USER YOUR_USERNAME;
   ```
3. Test connection manually with Python

### Hook errors

Check project-specific error logs:
```bash
cat .claude/logs/hook_errors.log
```

### Windows-Specific Issues

#### "~" paths not working

Windows doesn't expand `~` in command execution. The installer generates `settings.json` with absolute paths. If you see errors about `~/.claude/`, reinstall:
```powershell
.\scripts\install.ps1 -Force
```

#### Hooks not firing on Windows

1. Verify `settings.json` has absolute Windows paths with forward slashes and the correct Python binary (the installer auto-detects `python3` vs `python`):
   ```json
   "command": "python \"C:/Users/username/.claude/hooks/pre-tool-use.py\""
   ```
2. Restart Claude Code after installation

#### Task Scheduler daemon not running

```powershell
# Check task status
Get-ScheduledTask -TaskName 'ClaudePostgreSQLSync'

# View recent runs
Get-ScheduledTaskInfo -TaskName 'ClaudePostgreSQLSync'

# View logs
Get-Content "$env:USERPROFILE\.claude\logs\sync_daemon.log" -Tail 50

# Manually start the task
Start-ScheduledTask -TaskName 'ClaudePostgreSQLSync'

# Reinstall the daemon
.\scripts\install-task-scheduler.ps1
```

#### Python not found on Windows

Ensure Python is in your PATH. The installer checks these locations:
- `python3` / `python` commands
- `%LOCALAPPDATA%\Programs\Python\Python3XX\python.exe`
- `%ProgramFiles%\Python3XX\python.exe`

### Uninstalling

To uninstall while preserving logs and configuration:
```bash
# Windows (PowerShell)
.\scripts\install.ps1 -Uninstall

# macOS / Linux
./scripts/install.sh --uninstall
```

This removes:
- Hook scripts (except config)
- Commands, agents, skills
- settings.json (backed up)
- Background daemon

Preserved:
- `~/.claude/logs/` (activity logs)
- `~/.claude/gz-observability-memory/` (memory system)
- `~/.claude/hooks/auto-logger-config.json` (your settings)

---

## File Structure

```
optivai-claude-plugin/
├── .claude/
│   ├── commands/              # Slash commands & command handlers
│   │   ├── activity_commands.py   # Activity & Memory System commands
│   │   ├── commands.json          # Command configuration
│   │   ├── dispatcher.py          # Command dispatcher
│   │   ├── handoff.md             # Session continuity handoff skill
│   │   ├── prime-agent.md         # Context priming with Propulsion Check
│   │   ├── jira.md                # JIRA ticket management skill
│   │   ├── report.md              # PostgreSQL reporting skill
│   │   ├── ralph-loop.md          # Ralph Loop command
│   │   ├── cancel-ralph.md        # Cancel Ralph command
│   │   ├── connect-jira.md        # JIRA API reference patterns
│   │   ├── superpowers/           # Advanced workflow skills
│   │   │   ├── brainstorming.md
│   │   │   ├── test-driven-development.md
│   │   │   ├── systematic-debugging.md
│   │   │   ├── verification-before-completion.md
│   │   │   └── ... (14 skills total)
│   │   └── ... (additional commands)
│   └── settings.json          # Hook configuration example
├── agents/                    # Agent templates (20 agents)
│   ├── postgresql-specialist.md
│   ├── sql-developer.md
│   ├── etl-pipeline-developer.md
│   ├── data-architect.md
│   └── ... (see Agents section)
├── claude_lib/                # Client libraries (synced to ~/.claude/lib/)
│   └── jira_client.py         # JIRA API client for /jira skill
├── config/
│   ├── auto-logger-config.example.json
│   ├── jira-config.template.json   # JIRA config template
│   ├── settings-template-windows.json  # Windows path template
│   └── settings-template-unix.json     # Unix path template
├── docs/
│   ├── LOGGING_SYSTEM.md      # Detailed logging documentation
│   └── EXECUTIVE_SUMMARY.md   # Overview for stakeholders
├── scripts/
│   ├── install.sh             # Cross-platform installer (detects OS)
│   ├── install.ps1            # Windows PowerShell installer
│   ├── install-task-scheduler.ps1  # Windows Task Scheduler setup
│   ├── install-launchd.sh     # macOS Launch Agent installer
│   ├── update-skills.sh       # Quick sync (skills, agents, libs)
│   ├── log_writer.py          # Core logging module
│   ├── hooks/
│   │   ├── session_summary.py # Stop hook: token usage & cost tracking
│   │   └── log_writer.py      # Hook-deployed copy of core logger
│   ├── pre_tool_use.py        # PreToolUse hook
│   ├── user_prompt_submit.py  # UserPromptSubmit hook
│   ├── pg_sync.py      # PostgreSQL sync daemon
│   ├── open_brain.py          # Persistent Memory: semantic knowledge (CLI + Pi bridge)
│   ├── brain_transfer.py      # Memory Migration: legacy data to persistent memory
│   ├── memory_writer.py       # Memory system module
│   ├── subagent_context.py    # Subagent tracking module
│   ├── stop-hook.sh           # Ralph Loop Stop hook
│   ├── setup-ralph-loop.sh    # Ralph Loop setup script
│   ├── com.feedbackloopai.claude-pg-sync.plist  # macOS Launch Agent config
│   └── requirements.txt       # Python dependencies (incl. PyYAML)
├── sql/
│   ├── BRAIN_SCHEMA.sql             # Memory DDL (THOUGHTS table + views)
│   ├── VW_CLAUDE_CODE_VIEWS.sql    # Cost & analytics views (operation-based)
│   ├── VW_CLAUDE_CODE_TOKEN_VIEWS.sql  # Token usage & session summary views
│   └── VW_AGENT_SESSIONS_V2.sql    # Session aggregation views
├── skills/                    # Custom skills
├── _quarantine/               # Deprecated/unused components
├── .gitignore
└── README.md

# Files created at runtime in ~/.claude/
~/.claude/
├── commands/                  # Synced slash commands (from .claude/commands/)
│   └── superpowers/           # Superpowers workflow skills
├── agents/                    # Synced agent templates (from agents/)
├── lib/                       # Synced client libraries (from claude_lib/)
│   └── jira_client.py
├── config/                    # User configuration
│   └── jira-config.json       # JIRA config (from template)
├── secrets/                   # Credentials (user-created)
│   └── jira-token             # JIRA API token
├── hooks/                     # Hook scripts (from scripts/)
│   ├── open_brain.py          # Persistent Memory (deployed copy)
│   ├── brain_hook.py          # Auto-capture hook (deployed copy)
│   ├── till_done.py           # Till-Done mode hook (deployed copy)
│   └── auto-logger-config.json # PostgreSQL auth config
├── sql/                       # Schema DDL (deployed by installer)
│   └── BRAIN_SCHEMA.sql       # Memory system DDL
├── logs/                      # Activity logs
└── gz-observability-memory/   # Memory System (Legacy)
    ├── session_state.yaml     # Current session context
    ├── planned_tasks.yaml     # Task tracking
    ├── work_log.yaml          # Action history
    ├── recovery_checkpoint.yaml # Crash recovery
    └── handoff_context.yaml   # Session continuity (from /handoff)
```

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## License

MIT License - See LICENSE file for details.

---

## Support

- **Issues**: [GitHub Issues](https://github.com/feedbackloopai/optivai-claude-plugin/issues)
- **Documentation**: See `/docs` folder for detailed guides
