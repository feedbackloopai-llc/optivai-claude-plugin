# OptivAI Claude Code Plugin

**Version 2.0.0** | Plugin for [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

38 specialized agents, 37 workflow commands, persistent semantic memory, and graph-based task tracking -- all wired into Claude Code via hooks.

---

## What It Does

This plugin extends Claude Code with:

- **Activity Logging** -- hooks capture every tool operation and prompt, sync to PostgreSQL
- **Brain** -- persistent semantic memory (Neon PostgreSQL + pgvector), searchable by meaning
- **Beads** -- graph-based task tracking with dependencies, status flow, and workflow templates
- **Agents** -- 38 specialized agents across development, data, business, and strategy domains
- **Commands** -- 37 slash commands for memory, tasks, workflows, integrations, and session management
- **Superpowers** -- 14 discipline-enforcing workflow skills (TDD, debugging, planning, etc.)
- **Till-Done Mode** -- blocks tool execution until the agent plans its work
- **Ralph Loop** -- iterative development loop (test, refactor, fix) that runs autonomously

---

## Installation

```bash
cd ~/Documents/optivai-claude-plugin
bash scripts/install.sh
```

### What Gets Installed

| Component | Destination | Source |
|-----------|-------------|--------|
| Hook scripts | `~/.claude/hooks/` | `scripts/` |
| Slash commands | `~/.claude/commands/` | `.claude/commands/` |
| Agent templates | `~/.claude/agents/` | `agents/` |
| Client libraries | `~/.claude/lib/` | `claude_lib/` |
| Brain schema DDL | `~/.claude/sql/` | `sql/` |
| PostgreSQL config | `~/.claude/hooks/auto-logger-config.json` | Generated |
| Sync daemon (macOS) | `~/Library/LaunchAgents/` | `scripts/` via launchd |

### Installer Options

```bash
bash scripts/install.sh --skip-daemon   # Skip background daemon
bash scripts/install.sh --force         # Overwrite existing installation
bash scripts/install.sh --uninstall     # Remove installation (keeps logs)
```

---

## Configuration

### Required Environment Variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key (Brain metadata extraction via Haiku) |

### Optional Environment Variables

| Variable | Purpose |
|----------|---------|
| `JIRA_EMAIL` | Atlassian account email for JIRA integration |
| `JIRA_API_KEY` | Atlassian API token for JIRA integration |
| `JIRA_URL` | JIRA instance URL |

### macOS Keychain Storage (Recommended)

Store secrets in Keychain instead of environment variables or dotfiles:

```bash
# Store
security add-generic-password -a "$USER" -s "DATABASE_URL" \
  -w "postgresql://user:pass@host/dbname" -U

# Retrieve (in scripts or shell profile)
export DATABASE_URL=$(security find-generic-password -a "$USER" -s "DATABASE_URL" -w)
```

Add retrieval lines to `~/.zshrc` or `~/.zprofile` so they load automatically.

---

## Brain System

Persistent semantic memory backed by Neon PostgreSQL + pgvector. Thoughts are embedded as vectors and searchable by meaning, not keywords.

### How It Works

1. You capture a thought (or the hook auto-captures on decision/preference/learning signals)
2. Claude Haiku extracts metadata (type, topics, people, summary, confidence)
3. `sentence-transformers` generates a 768-dimensional embedding
4. Stored in `brain.thoughts` with pgvector for cosine similarity search

### Commands

| Command | What It Does |
|---------|-------------|
| `/brain-capture <text>` | Store a thought with auto-extracted metadata |
| `/brain-search <query>` | Semantic search across all memories |
| `/brain-recent` | Show recent memories (default: 7 days) |
| `/brain-stats` | Memory distribution and statistics |
| `/brain-context` | Recall recent memory + pending tasks (session start) |
| `/brain-timeline <topic>` | Chronological view of memories on a topic |

### CLI

```bash
python3 scripts/open_brain.py --capture "your thought here"
python3 scripts/open_brain.py --search "query by meaning" --limit 10
python3 scripts/open_brain.py --recent --days 7
python3 scripts/open_brain.py --stats
```

### Auto-Capture Triggers

The `brain_hook.py` fires automatically on:

- **Decision signals**: "I decided", "key decision", "let's go with"
- **Preference signals**: "always do", "never do", "I prefer", "from now on"
- **People signals**: "meeting note", "talked to", known names + "said"
- **Learning signals**: "lesson learned", "gotcha", "next time...should"
- **Explicit requests**: "remember this", "note to self"
- **File writes** to `docs/plans/`, `docs/decisions/`, `docs/adr/`, `ARCHITECTURE.md`

### Thought Types

`decision` | `insight` | `person_note` | `meeting` | `idea` | `task` | `reflection` | `preference` | `impression` | `pattern` | `working_memory`

---

## Beads System

Graph-based task tracking. Beads are the single source of truth for all task-driven work.

### Status Flow

```
OPEN --> IN_PROGRESS --> DONE --> CLOSED
```

- **OPEN** -- created, not started
- **IN_PROGRESS** -- actively being worked on
- **DONE** -- work complete, pending verification
- **CLOSED** -- verified complete (the only state that means "finished")

**Ready beads** = OPEN + all dependencies DONE/CLOSED. Only ready beads should be worked on.

### Commands

| Command | What It Does |
|---------|-------------|
| `/bead-create <title>` | Create a new bead |
| `/bead-ready` | Show unblocked beads ready for work |
| `/bead-update <id>` | Update bead status or details |
| `/bead-show <id>` | Full details on a bead |
| `/bead-list` | List beads (filterable by status, label) |
| `/bead-link <id> <dep-id>` | Wire dependency between beads |

### CLI

```bash
beads create "Task title"
beads ready
beads update <id> --status in_progress
beads close <id>
beads depend <id> <dep-id>
beads list --status open
beads show <id>
```

### Storage

- **Project beads**: `.beads/` in project directory
- **Global beads**: `~/.claude/beads/` (cross-project)
- **Format**: JSONL (append-only, git-friendly)
- **Concurrency**: FileLock

### Molecules

Workflow templates that instantiate as dependency-wired bead graphs. Define steps with `## Step: ref`, `Needs: dep1, dep2`, `Tier: sonnet`. Each step becomes a bead with dependencies auto-wired on instantiation.

Use `/mol-pour` to instantiate a molecule.

---

## Commands Reference

| Category | Commands |
|----------|----------|
| **Memory** | `/brain-capture`, `/brain-search`, `/brain-recent`, `/brain-stats`, `/brain-context`, `/brain-timeline` |
| **Beads** | `/bead-create`, `/bead-ready`, `/bead-update`, `/bead-show`, `/bead-list`, `/bead-link`, `/mol-pour` |
| **Workflows** | `/ralph-loop`, `/cancel-ralph`, `/tilldone`, `/superpowers`, `/create-prp` |
| **Session** | `/prime-agent`, `/quick-context`, `/new-session`, `/load-context`, `/export-context`, `/handoff`, `/summary`, `/context-check` |
| **Integrations** | `/jira`, `/connect-jira`, `/tana`, `/excalidraw-diagram` |
| **Infrastructure** | `/sync-now`, `/sync-status`, `/db-connect`, `/config-logs`, `/validate-event`, `/activity-query`, `/prime-project`, `/repair-excel-encoding` |

---

## Superpowers

14 workflow skills that enforce discipline on complex tasks.

| Skill | When to Use |
|-------|-------------|
| `brainstorming` | New features, creative work, unclear requirements |
| `writing-plans` | Multi-step tasks (3+ files, architectural decisions) |
| `executing-plans` | Running approved plans with checkpoints |
| `test-driven-development` | Building testable features or fixing bugs |
| `systematic-debugging` | Investigating failures before proposing fixes |
| `verification-before-completion` | Before claiming "done" |
| `requesting-code-review` | After completing major features |
| `receiving-code-review` | Processing review feedback |
| `dispatching-parallel-agents` | 2+ independent tasks, no shared state |
| `subagent-driven-development` | Complex work via specialized subagents |
| `finishing-a-development-branch` | Branch cleanup and merge prep |
| `using-git-worktrees` | Parallel branch work |
| `using-superpowers` | Meta: how to use this system |
| `writing-skills` | Creating new slash command skills |

---

## Agents Reference

### Development

| Agent | Specialty |
|-------|-----------|
| `senior-engineer` | General senior engineering |
| `implementation-developer` | Production-ready code |
| `implementer` | Task execution |
| `sql-developer` | Complex SQL, stored procedures |
| `database-administrator` | DB operations, tuning |
| `devops-deployment-specialist` | CI/CD, deployments |
| `ui-ux-frontend-engineer` | Frontend development |

### Data

| Agent | Specialty |
|-------|-----------|
| `data-architect` | Schema design, data modeling |
| `data-engineer` | Data infrastructure |
| `data-scientist` | Analysis, statistics, ML |
| `data-quality-analyst` | Data quality assessment |
| `data-quality-manager` | Data quality programs |
| `data-steward` | Data governance execution |
| `data-governance-lead` | Data governance strategy |
| `etl-pipeline-developer` | Data pipelines, transformations |
| `machine-learning-engineer` | ML model development |
| `postgresql-specialist` | PostgreSQL: indexes, partitioning, pgvector |
| `docs-scraper` | Documentation extraction |

### Architecture and Quality

| Agent | Specialty |
|-------|-----------|
| `solution-architect-planner` | Technical planning |
| `solution-architect` | System architecture |
| `code-quality-reviewer` | Code review against standards |
| `superpowers-code-reviewer` | Structured code review workflow |
| `quality-reviewer` | General quality assessment |
| `spec-reviewer` | Specification review |
| `prompt-engineer-optimizer` | Prompt engineering |
| `technical-writer` | Technical documentation |

### Business and Strategy

| Agent | Specialty |
|-------|-----------|
| `product-manager` | Product strategy and roadmap |
| `product-owner` | Backlog and requirements |
| `program-manager` | Program coordination |
| `business-analyst` | Business analysis |
| `financial-analyst` | Financial modeling |
| `market-analysis-mgr` | Market research |
| `strategic-planning-manager` | Strategic planning |
| `user-research-expert` | User research |
| `ux-ui-design-manager` | UX/UI design strategy |
| `change-management-specialist` | Organizational change |
| `compliance-officer` | Compliance and risk |
| `immigration-law-sme` | Immigration law |
| `subject-matter-expert` | Domain expertise |

---

## Architecture

```
Claude Code CLI
    |
    v
Hooks (PreToolUse, UserPromptSubmit, Stop)
    |
    +---> Local JSON logs (~/.claude/logs/)
    |         |
    |         v
    |     pg_sync.py (launchd, every 60s)
    |         |
    |         v
    |     Neon PostgreSQL (raw_events --> activity_stream)
    |
    +---> brain_hook.py (auto-capture on decision/preference/learning signals)
    |         |
    |         v
    |     open_brain.py --> Haiku metadata --> pgvector embeddings
    |         |
    |         v
    |     brain.thoughts (Neon PostgreSQL)
    |
    +---> beads_writer.py (auto-creates beads for write/edit/task operations)
              |
              v
          .beads/ (JSONL, project-local) + ~/.claude/beads/ (global)
```

### Data Flow

| Stage | Latency | Description |
|-------|---------|-------------|
| Local logs | Instant | Written by hooks on every operation |
| raw_events | ~60 seconds | pg_sync daemon pushes every minute |
| activity_stream | ~15 minutes | Normalized view with refresh cycle |
| Brain capture | Instant | Embedding + metadata on capture |

### Sync Daemon

`pg_sync.py` runs as a macOS Launch Agent (launchd). It reads local JSON logs and pushes them to Neon PostgreSQL. Manual trigger: `/sync-now`.

---

## Updating

```bash
cd ~/Documents/optivai-claude-plugin
git pull
bash scripts/install.sh
```

For commands and agents only (faster):

```bash
bash scripts/update-skills.sh
```

---

## File Structure

```
optivai-claude-plugin/
├── .claude/commands/          # 37 slash commands
│   └── superpowers/           # 14 workflow skills
├── agents/                    # 38 agent templates
├── claude_lib/                # Client libraries
├── config/                    # Configuration templates
├── scripts/
│   ├── install.sh             # Cross-platform installer
│   ├── pg_sync.py             # PostgreSQL sync daemon
│   ├── open_brain.py          # Brain CLI (capture, search, recall)
│   ├── brain_hook.py          # Auto-capture hook
│   ├── beads_writer.py        # Auto-bead creation hook
│   ├── pre_tool_use.py        # PreToolUse hook
│   ├── user_prompt_submit.py  # UserPromptSubmit hook
│   ├── hooks/session_summary.py  # Stop hook (token/cost tracking)
│   └── requirements.txt       # Python dependencies
├── sql/
│   ├── BRAIN_SCHEMA.sql       # Memory DDL
│   └── VW_CLAUDE_CODE_*.sql   # Analytics views
└── README.md
```

---

## License

MIT License -- see LICENSE file.

## Support

[GitHub Issues](https://github.com/feedbackloopai/optivai-claude-plugin/issues)
