# OptivAI Claude Code Plugin

**Version 2.0.0** | Plugin for [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

38 specialized agents, 43 workflow commands, persistent semantic memory, and graph-based task tracking -- all wired into Claude Code via hooks. Installs on **macOS, Linux, WSL2, and native Windows**.

---

## What It Does

This plugin extends Claude Code with:

- **Activity Logging** -- hooks capture every tool operation and prompt, sync to PostgreSQL
- **Brain** -- persistent semantic memory (Neon PostgreSQL + pgvector), searchable by meaning
- **Beads** -- graph-based task tracking with dependencies, status flow, and workflow templates
- **Agents** -- 38 specialized agents across development, data, business, and strategy domains
- **Commands** -- 43 slash commands for memory, tasks, workflows, integrations, and session management
- **Superpowers** -- 14 discipline-enforcing workflow skills (TDD, debugging, planning, etc.)
- **Till-Done Mode** -- blocks tool execution until the agent plans its work
- **Ralph Loop** -- iterative development loop (test, refactor, fix) that runs autonomously

---

## Prerequisites

Have these ready **before** running the installer:

| Requirement | Notes |
|-------------|-------|
| **Python 3.9+** (3.11+ recommended) | Check with `python3 --version` (macOS/Linux/WSL) or `python --version` (Windows). The brain hooks, sync daemon, and Beads CLI are all Python. |
| **A PostgreSQL database with the `pgvector` extension** | [Neon](https://neon.tech) is recommended -- the **free tier works**. The `pgvector` extension must be enabled (`CREATE EXTENSION vector;`). Use the **direct** endpoint, not the pooler -- pgvector requires the direct connection. |
| **An Anthropic API key** | Used for brain metadata extraction (type, topics, people, summary) via Claude Haiku. Get one at [console.anthropic.com](https://console.anthropic.com/). |
| **git** | For cloning and updating the plugin repo. |
| **Node 18+** | *Pi plugin only* -- not required for this Claude Code plugin. |

> **First-capture model download:** the brain uses `sentence-transformers` for local embeddings.
> The first time you capture a thought, it downloads the embedding model and its PyTorch
> dependency (**~420 MB**). This is a one-time download, cached under your home directory.

---

## Credentials

The brain needs **two** values to function. Without both, it cannot connect and capture/search will fail:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string (Neon + pgvector). Format: `postgresql://user:pass@host/db?sslmode=require` |
| `ANTHROPIC_API_KEY` | Anthropic API key (brain metadata extraction via Haiku). Format: `sk-ant-...` |

### macOS -- Keychain (recommended) or env vars

macOS can store the database URL in the login Keychain so it never lives in a dotfile. The
canonical Keychain service name is **`optivai-neon-database-url`**:

```bash
# Store the DSN in Keychain (one time)
security add-generic-password -a "$USER" -s optivai-neon-database-url -w "postgresql://user:pass@host/db?sslmode=require"

# Retrieve it into your shell profile (~/.zshrc or ~/.zprofile)
export DATABASE_URL="$(security find-generic-password -a "$USER" -s optivai-neon-database-url -w)"
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or skip Keychain entirely and just export both in your shell profile:

```bash
export DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Linux / WSL2 -- environment variables

There is no Keychain on Linux or WSL. Add both exports to `~/.bashrc` or `~/.zshrc` so they
persist across sessions:

```bash
export DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Windows (native) -- `setx`

On native Windows, set them as **persistent user environment variables** with `setx` (open a
new PowerShell window afterward so they take effect):

```powershell
setx DATABASE_URL      "postgresql://user:pass@host/db?sslmode=require"
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Or use **System Properties -> Environment Variables -> User variables**.

---

## Installation

Clone the repo, then run the installer for your platform. The installer auto-detects your OS and
takes the right path -- but the per-platform notes below tell you exactly what to expect.

```bash
git clone https://github.com/feedbackloopai/optivai-claude-plugin.git
cd optivai-claude-plugin
```

### macOS

```bash
bash scripts/install.sh
```

- Uses **Keychain or env vars** for credentials (see [Credentials](#credentials) above).
- Installs the PostgreSQL sync daemon as a **launchd** Launch Agent (runs every 60s).
- Copies hooks to `~/.claude/hooks`, commands/agents/skills into `~/.claude/`, and the brain
  schema into `~/.claude/sql/`.

### Linux

```bash
bash scripts/install.sh
```

- Uses **environment variables** for credentials (no Keychain).
- Installs the sync daemon as a **systemd user service**, falling back to **cron** if systemd
  user services aren't available.
- Same `~/.claude/` layout as macOS.

### WSL2 (recommended path for most Windows users)

Run **inside your WSL2 Ubuntu shell**, exactly like Linux:

```bash
bash scripts/install.sh
```

- The installer **auto-detects WSL** (via `/proc/version` / `/proc/sys/kernel/osrelease`) and
  prints WSL-specific guidance.
- Uses **environment variables** for credentials (Keychain is not available in WSL).
- Daemon: launchd and systemd are typically unavailable in WSL, so the installer uses a
  **cron** job and also prints a **Windows Task Scheduler** recipe (which keeps syncing even
  when no WSL shell is open). You can always run the sync by hand:
  `python3 ~/.claude/hooks/pg_sync.py --once`.
- **`~/.claude` lives inside the WSL filesystem** (your Linux home), not on the Windows C: drive.

### Windows (native, no WSL)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

- Copies hooks/commands/agents/skills to **`%USERPROFILE%\.claude`**.
- Installs Python dependencies via **`pip install -r scripts\requirements.txt`**.
- Merges `settings.json` safely (same merge logic as the bash installer -- see below).
- Installs the sync daemon via **Windows Task Scheduler** (`OptivAI-PgSync`, runs every 5 min).
- Set `DATABASE_URL` / `ANTHROPIC_API_KEY` via **`setx`** (see [Credentials](#credentials)).

> **Honest caveat:** the native-Windows installer (`install.ps1`) is newer and was authored by
> cross-referencing `install.sh`. It has **not yet had a full Windows smoke test**, so a path or
> step may need adjustment on your machine. If you have WSL2 available, the **WSL2 route above is
> the more battle-tested way to run this plugin on Windows.**

---

## settings.json Safety

> **The installer MERGES into your existing `~/.claude/settings.json` -- it never clobbers it.**
>
> - It **preserves** your hooks, env vars, model choice, permissions, `enabledPlugins`, and every
>   other top-level key. It adds only the plugin's own hooks (and the `CLAUDE_USER_EMAIL` /
>   `CLAUDE_ORG_NAME` / agent-teams env keys).
> - It **backs up** `settings.json` to a timestamped `*.pre-merge-backup.*` file before writing.
> - **Re-running is a safe no-op** -- already-present hooks are detected and skipped, and stale
>   command spellings from older plugin versions are converged to a single canonical entry.
> - **Upgrading the plugin never destroys your configuration.**
> - **Uninstall is surgical** -- it removes *only* the plugin's hook commands (with a
>   `*.uninstall-backup.*` snapshot first) and leaves everything else untouched.

The merge is performed by `scripts/merge_settings.py` (stdlib-only Python, identical on every
platform). If your `settings.json` ever contains invalid JSON, the merge backs it up to a
`*.corrupt-*` file and starts from a clean baseline rather than failing the install.

---

## Installer Options

```bash
bash scripts/install.sh --skip-daemon   # Skip the background sync daemon (run pg_sync.py manually)
bash scripts/install.sh --force         # Re-run the install (re-merges settings.json idempotently)
bash scripts/install.sh --uninstall     # Remove plugin files + hooks (keeps logs and your data)
bash scripts/install.sh --help          # Show usage
```

On native Windows the same flags are PowerShell switches:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipDaemon
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Force
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Uninstall
```

> **`--force` no longer destroys `settings.json`.** It simply re-runs the idempotent merge, so a
> fresh re-install is the empty-merge case and your existing settings survive untouched.

---

## Python Packages

The installer runs `pip install -r scripts/requirements.txt` for you (and you can run it manually
any time). The packages and what they're for:

| Package | Used for |
|---------|----------|
| `psycopg2-binary` | PostgreSQL connection (brain + activity sync) |
| `pgvector` | Vector type support for pgvector similarity search |
| `sentence-transformers` | Local 768-dim embeddings (the ~420 MB first-use download) |
| `anthropic` | Brain metadata extraction via Claude Haiku |
| `PyYAML` | Memory-system YAML files |
| `jsonpatch` | RFC 6902 diffs for the rollback (RB) primitive |
| `click`, `filelock` | Beads CLI (command parsing + concurrency locking) |

Manual install if you ever need it:

```bash
pip install -r scripts/requirements.txt
```

---

## Verify the Install

1. **Brain connects:**

   ```bash
   python3 scripts/open_brain.py --stats
   ```

   (On native Windows: `python scripts\open_brain.py --stats`.) This should connect to your
   database and print memory distribution. If it errors on connection, re-check `DATABASE_URL`.

2. **Hooks are wired:** confirm the plugin's hooks appear in `~/.claude/settings.json` (look for
   `context_primer.py`, `pre-tool-use.py`, `user-prompt-submit.py`, `session_summary.py`,
   `stop-hook.sh`).

3. **Commands work:** in a Claude Code session, run `/brain-search test` -- it should execute the
   brain search command. (Restart Claude Code / start a new conversation after installing so the
   hooks load.)

4. **Daemon is running** (optional):
   - macOS: `launchctl list | grep claude-pg`
   - Linux: `systemctl --user status claude-pg-sync`
   - WSL: `crontab -l | grep pg_sync`
   - Windows: `schtasks /query /tn OptivAI-PgSync`

---

## What Gets Installed

| Component | Destination | Source |
|-----------|-------------|--------|
| Hook scripts | `~/.claude/hooks/` | `scripts/` and `scripts/hooks/` |
| PII-redaction package | `~/.claude/hooks/redact/` | `scripts/redact/` |
| Slash commands | `~/.claude/commands/` | `.claude/commands/` |
| Agent templates | `~/.claude/agents/` | `agents/` |
| Skills | `~/.claude/skills/` | `skills/` |
| Brain schema DDL | `~/.claude/sql/` | `sql/BRAIN_SCHEMA_PG.sql`, `sql/VW_CLAUDE_CODE_VIEWS_PG.sql` |
| PostgreSQL/activity config | `~/.claude/hooks/auto-logger-config.json` | Generated |
| Plugin hooks/env | `~/.claude/settings.json` | Merged in place (never clobbered) |
| Sync daemon | launchd (macOS) / systemd or cron (Linux/WSL) / Task Scheduler (Windows) | `scripts/pg_sync.py` |

---

## Configuration

### Optional Environment Variables

| Variable | Purpose |
|----------|---------|
| `JIRA_EMAIL` | Atlassian account email for JIRA integration |
| `JIRA_API_KEY` | Atlassian API token for JIRA integration |
| `JIRA_URL` | JIRA instance URL |
| `CLAUDE_USER_EMAIL` | Token-usage attribution (set by the installer prompt) |
| `CLAUDE_ORG_NAME` | Organization attribution (defaults to `FeedbackLoopAI`) |

---

## Brain System

Persistent semantic memory backed by Neon PostgreSQL + pgvector. Thoughts are embedded as vectors and searchable by meaning, not keywords.

### How It Works

1. You capture a thought (or the hook auto-captures on decision/preference/learning signals)
2. Claude Haiku extracts metadata (type, topics, people, summary, confidence)
3. `sentence-transformers` generates a 768-dimensional embedding
4. Stored in `brain.thoughts` with pgvector for cosine similarity search

### NAL-lite truth values

Every atom carries an `stv: {f, c}` -- NAL **frequency** (degree of positive evidence, 0-1) and
**confidence** (weight of evidence, 0-1). Search results flag atoms with `c < 0.35` as
`[LOW-CONFIDENCE]`. Seed explicit values at capture with `--stv-f` / `--stv-c`; otherwise `c` is
derived from the LLM confidence label (high=0.9, medium=0.7, low/absent=0.5). `/brain-revise A B`
fuses two atoms about the same proposition via NAL evidential-horizon revision -- the derived
atom's confidence is strictly higher than either premise, and `derives_from` links make the
resolution auditable via `/brain-trace`. This is NAL-lite (revision + evidence propagation only),
not a general inference engine.

### Commands

| Command | What It Does |
|---------|-------------|
| `/brain-capture <text>` | Store a thought with auto-extracted metadata |
| `/brain-search <query>` | Semantic search across all memories |
| `/brain-recent` | Show recent memories (default: 7 days) |
| `/brain-stats` | Memory distribution and statistics |
| `/brain-context` | Recall recent memory + pending tasks (session start) |
| `/brain-timeline <topic>` | Chronological view of memories on a topic |
| `/brain-revise <A> <B>` | Fuse two atoms about the same proposition (NAL revision) |
| `/brain-trace <id>` | Walk a memory's provenance chain |
| `/brain-inspect <id>` | Inspect a memory's state at a point in time |
| `/brain-promote <id>` | Hebbian promote (boost retrieval ranking) |
| `/brain-forget <id>` | Verified-forget a memory (MS_ε guarantee) |
| `/brain-replay` | Replay the session audit log |

### CLI

```bash
python3 scripts/open_brain.py --capture "your thought here"
python3 scripts/open_brain.py --search "query by meaning" --limit 10
python3 scripts/open_brain.py --recent --days 7
python3 scripts/open_brain.py --stats
python3 scripts/open_brain.py --init     # create schema + table (first-time setup)
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

- **Canonical store**: one physical JSONL DB at `~/.beads/issues.jsonl`. The `beads` CLI resolves
  it by walking up from the current directory, so every repo shares the same store.
- **Never run `beads init` inside a repo** -- it creates a local `.beads/` that shadows the
  canonical store and re-splits the tracker.
- **Format**: JSONL (append-only, git-friendly).
- **Concurrency**: FileLock.

### Molecules

Workflow templates that instantiate as dependency-wired bead graphs. Define steps with `## Step: ref`, `Needs: dep1, dep2`, `Tier: sonnet`. Each step becomes a bead with dependencies auto-wired on instantiation.

Use `/mol-pour` to instantiate a molecule.

---

## Commands Reference

| Category | Commands |
|----------|----------|
| **Memory** | `/brain-capture`, `/brain-search`, `/brain-recent`, `/brain-stats`, `/brain-context`, `/brain-timeline`, `/brain-revise`, `/brain-trace`, `/brain-inspect`, `/brain-promote`, `/brain-forget`, `/brain-replay` |
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
| `sql-developer` | Complex SQL, stored procedures, pgvector |
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
Hooks (SessionStart, PreToolUse, UserPromptSubmit, Stop)
    |
    +---> Local JSON logs (~/.claude/logs/)
    |         |
    |         v
    |     pg_sync.py (launchd / systemd / cron / Task Scheduler)
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
          ~/.beads/issues.jsonl (canonical JSONL store)
```

### Data Flow

| Stage | Latency | Description |
|-------|---------|-------------|
| Local logs | Instant | Written by hooks on every operation |
| raw_events | ~60 seconds | pg_sync daemon pushes every minute |
| activity_stream | ~15 minutes | Normalized view with refresh cycle |
| Brain capture | Instant | Embedding + metadata on capture |

### Sync Daemon

`pg_sync.py` reads local JSON logs and pushes them to Neon PostgreSQL. It runs as a launchd Launch
Agent on macOS, a systemd user service (or cron) on Linux, cron / Windows Task Scheduler under
WSL, and Windows Task Scheduler on native Windows. Manual trigger: `/sync-now` (or
`python3 ~/.claude/hooks/pg_sync.py --once`).

---

## Updating

```bash
cd optivai-claude-plugin
git pull
bash scripts/install.sh
```

Re-running the installer is safe: it re-merges `settings.json` idempotently (your configuration is
preserved -- see [settings.json Safety](#settingsjson-safety)) and upgrades the brain schema in
place if needed.

For commands and agents only (faster):

```bash
bash scripts/update-skills.sh
```

---

## File Structure

```
optivai-claude-plugin/
├── .claude/commands/          # slash command sources
│   └── superpowers/           # 14 workflow skills
├── agents/                    # 38 agent templates
├── claude_lib/                # client libraries (used by some scripts)
├── config/                    # configuration templates
├── skills/                    # bundled skills (e.g. ai-harness-audit)
├── scripts/
│   ├── install.sh             # cross-platform installer (macOS/Linux/WSL)
│   ├── install.ps1            # native-Windows PowerShell installer
│   ├── merge_settings.py      # safe settings.json merge/unmerge helper
│   ├── requirements.txt       # Python dependencies
│   ├── pg_sync.py             # PostgreSQL sync daemon
│   ├── open_brain.py          # Brain CLI (capture, search, recall, revise)
│   ├── citation_walker.py     # citation-graph walker (time-travel)
│   ├── time_travel.py         # temporal memory retrieval hook
│   ├── vf_probe.py            # VF_ε verified-forget probe runner
│   ├── redact/                # PII/secret redaction package
│   └── hooks/                 # hook scripts (pre-tool-use, session_summary, ...)
├── sql/
│   ├── BRAIN_SCHEMA_PG.sql        # Memory DDL (PostgreSQL)
│   ├── KNOWLEDGE_GRAPH_PG.sql     # Knowledge-graph DDL
│   └── VW_CLAUDE_CODE_VIEWS_PG.sql  # Analytics views
└── README.md
```

---

## License

MIT License -- see LICENSE file.

## Support

[GitHub Issues](https://github.com/feedbackloopai/optivai-claude-plugin/issues)
