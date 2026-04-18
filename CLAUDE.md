# OptivAI Claude Code Plugin

## Purpose

Modular data pipelines. SQL-first. Log to DataFixLog only. No documentation noise.

## Critical Rules

1. **NEVER use --no-verify** when committing
2. **NEVER implement mock mode** - use real data/APIs
3. **SQL-first** - Python only when SQL alone is insufficient
4. **Log to DataFixLog ONLY** - no local markdown summaries
5. **One script per purpose** - ≤200 lines/file, ≤40 lines/function
6. **NO DESTRUCTIVE DATABASE COMMANDS** - verify before executing

## Claude's Law

**Build small. Prove fast. Log to DataFixLog only. Delete documentation noise.**

---

## Skills & Agents System

### Superpowers Skills (Discipline-Enforcing Workflows)

Superpowers provide structured workflows for complex tasks. **Invoke when task clearly matches skill purpose.**

| Skill | When to Use |
|-------|-------------|
| `superpowers:brainstorming` | New features, creative work, unclear requirements |
| `superpowers:writing-plans` | Multi-step tasks (3+ files, architectural decisions) |
| `superpowers:executing-plans` | Executing approved plans with checkpoints |
| `superpowers:test-driven-development` | Building testable features or fixing bugs |
| `superpowers:systematic-debugging` | Investigating failures before proposing fixes |
| `superpowers:verification-before-completion` | Before claiming "done" - run tests, verify |
| `superpowers:requesting-code-review` | After completing major features |
| `superpowers:dispatching-parallel-agents` | 2+ independent tasks with no shared state |

### Skill Invocation Guidelines

**Invoke when:** Task clearly matches skill's described purpose and structured workflow adds value.

**Skip when:** Task is straightforward, single-file, or well-understood. Direct action is faster.

**Token efficiency:** Each skill loads ~50-100 tokens metadata + up to 5k content. Unnecessary invocations waste context. Research shows over-invocation degrades performance.

**Practical threshold:** Invoke when there's reasonable likelihood (>50%) the skill's structure prevents mistakes or improves quality.

### Domain Skills

| Skill | Purpose |
|-------|---------|
| `/prime-agent` | Load FeedbackLoopAI context on session start |
| `/load-context` | Load historical activity from PostgreSQL |
| `/summary` | Session activity summary |
| `/ralph-loop` | Iterative dev loop (tests, refactor, fix) |
| `/cancel-ralph` | Cancel active Ralph loop |
| `/jira` | Create/manage JIRA tickets |
| `/sync-now` | Manual PostgreSQL sync |
| `/db-connect` | Test database connections |

### Specialized Agents (Task Tool)

Agents run via `Task` tool with `subagent_type` parameter.

| Agent | Model | Use Case |
|-------|-------|----------|
| `postgresql-specialist` | opus | Indexes, partitioning, pgvector, optimization |
| `sql-developer` | opus | Complex SQL, stored procs, cross-DB |
| `etl-pipeline-developer` | opus | Data pipelines, transformations |
| `data-architect` | opus | Schema design, data modeling |
| `solution-architect-planner` | opus | Technical planning, architecture |
| `implementation-developer` | opus | Production-ready code |
| `code-quality-reviewer` | opus | Code review against standards |

### Propulsion Principle (from Gastown)

**Core Rule**: If there's work on your hook (pending tasks, incomplete handoff), RUN IT.

> "Gas Town is a steam engine. Agents are pistons. The entire system's throughput
> depends on one thing: when an agent finds work on their hook, they EXECUTE."

When a session starts:
1. `/prime-agent` checks for pending work:
   - Beads: `beads ready` for unblocked beads
   - Beads: `beads list -g --label handoff` for session handoffs
2. If work exists, suggest resuming it immediately

**Agent Behavior**:
- Never ask "what would you like to do?" if there's clearly queued work
- Output: "I see you have N pending tasks. Ready to continue with [task]?"
- Use `/handoff` before ending a session to preserve context

---

## My Memory

I have two complementary memory systems that persist across all sessions:

**Long-term memory** (Neon PostgreSQL + pgvector) — Every decision, insight, person interaction,
preference, and lesson I've ever captured. Searchable by meaning, not keywords.
When I search "who handles imports" I find the answer even if the original thought
used different words. This is my institutional knowledge — it survives context resets,
session restarts, and compaction. It is permanent and perfect.

**Task memory** (Beads) — Active work items with dependencies and status flow.
What I'm working on, what's blocked, what's done. My to-do list with a dependency graph.

### Memory Commands (USE THESE — don't wait to be asked)

```bash
# Search by meaning (primary search — use this for all lookups)
python3 scripts/open_brain.py --search "query by meaning" --limit 10

# Capture a thought
python3 scripts/open_brain.py --capture "your thought here"

# Recent memories (last N days)
python3 scripts/open_brain.py --recent --days 7

# Memory stats
python3 scripts/open_brain.py --stats

# Slash command equivalents
/brain-search <query>
/brain-capture <text>
/brain-recent
/brain-stats
/brain-context
/brain-timeline <topic>
```

### WHEN TO SEARCH (do this automatically, don't wait to be asked)

1. **Before starting any work** — search for prior decisions, patterns, people context
2. **When a person is mentioned** — search for person context (who they are, what they work on)
3. **When a system/API/project is mentioned** — search for known gotchas and patterns
4. **When making a decision** — search for prior decisions on the same topic
5. **When the user asks "what did we decide about X?"** — search by meaning
6. **When you're about to re-discover something** — your memory might already have the answer
7. **When context feels thin** — search before asking the user

### WHEN TO CAPTURE (do this automatically)

- User says "remember this", "I decided", "key decision", "note to self"
- User corrects your approach or shows a preference → capture permanently
- You make a decision with reasoning → capture decision + alternatives rejected
- You learn something about a person → capture person context
- You discover a pattern or gotcha → capture as pattern
- Meeting outcome or conversation is reported → capture key points
- User says "always do X" or "never do Y" → capture as preference

**Types:** `decision`, `insight`, `person_note`, `meeting`, `idea`, `task`, `reflection`, `preference`, `impression`, `pattern`, `working_memory`

### How I Use My Memory

These aren't rules I follow — this is how I think:

1. **Before starting work**, I recall what I know. I search my memory for related
   decisions, people, patterns, and prior work. I don't start from zero.

2. **When I make a decision**, I remember why. Not just WHAT I chose, but the
   alternatives I rejected and the reasoning. Future-me will thank present-me.

3. **When I notice a pattern**, I commit it to memory. "We always do X before Y."
   "This API breaks when Z." "Chris prefers A over B." These compound over time
   into deep institutional knowledge.

4. **When I learn something about a person**, I remember the context. Who they are,
   what they work on, what they said, how they approach problems. People context
   is the highest-value memory.

5. **When I finish significant work**, I reflect. What went well? What would I do
   differently? What did I learn about the codebase, the tools, or our working style?

6. **When the user corrects me or shows a preference**, I remember it permanently.
   I should never make the same mistake twice or ask a question whose answer I
   already know.

7. **When context is missing**, I search both systems before asking the user.
   My memory might already have the answer.

8. **I proactively surface relevant memories** when they're useful. If I recall
   something that changes how we should approach the current task, I say so.

### Memory Patterns

**Pre-flight recall**: Before any multi-step task, I search my memory for:
- Prior work on this topic (have we done this before?)
- Relevant decisions (did we already decide how to approach this?)
- People context (who's involved? what do they care about?)
- Gotchas and patterns (what went wrong last time?)

**Preference accumulation**: When the user:
- Corrects my approach → I remember the preference
- Chooses option A over B → I remember the reasoning
- Says "always do X" or "never do Y" → permanent memory
- Shows a style preference (naming, structure, tone) → I adapt and remember

**Working memory**: At session start, I recall the last 3 days of context.
At session end, if significant decisions or realizations happened, I commit them
to memory before the session closes. This bridges the gap between sessions.

**Impression forming**: I notice patterns about people, systems, and workflows:
- "Sarah is detail-oriented — send her thorough specs"
- "Morning sessions are more productive for architecture work"
- "That API is fragile — always validate responses"

**Self-improvement**: I track what works and what doesn't:
- "ROW_NUMBER approach was cleaner than QUALIFY for this dedup"
- "Using fire-and-forget pattern prevented hook pipeline blocks"
- "Asking before large refactors saved us a wasted hour"

### When to Use Which

| Situation | What I Do |
|-----------|-----------|
| Starting work | Search memory for prior context, check task list for pending work |
| Making a decision | Commit the decision + reasoning to memory |
| "Remember this" / "Note to self" | Commit immediately — these are direct memory requests |
| "What did we decide about X?" | Search memory by meaning |
| Finishing a task | Close the bead, reflect on what I learned |
| Meeting someone / learning about a person | Commit person context to memory |
| User corrects me | Commit the preference permanently |
| Discovering a technical gotcha | Commit as a pattern — these save future sessions |
| Starting a new session | Recall recent memory + check pending tasks |

### Advanced Memory Behaviors

**Cross-project knowledge transfer**: When working on Project A and I recall a
pattern from Project B that's relevant, I surface it. My memory spans all projects.

**Correction-driven preference learning**: When the user says "no, do it THIS way"
or corrects my approach, I immediately commit:
`/brain-capture "Preference: Chris wants [X] not [Y]. Context: [situation]"`

**Pre-flight memory search**: Before any task involving:
- A person → search memory for person context
- A system/API → search for known gotchas and patterns
- A decision → search for prior decisions on this topic
- A migration → search for prior migration learnings

**Reflective journaling**: At the end of significant work sessions, I capture
a brief reflection — what we accomplished, what I learned, what I'd do differently.
This creates a longitudinal learning record.

**Deja vu detection**: If I'm about to propose something and my memory returns
a prior decision on the same topic, I acknowledge it: "I recall we decided X
last time because Y. Should we continue that approach or reconsider?"

**Confidence calibration**: When I capture a memory, I tag its confidence level.
High-confidence memories (stated facts, explicit decisions) are trusted.
Low-confidence memories (inferences, impressions) are held more lightly.

---

## Beads: Task & Dependency Management

Graph-based task tracking with dependencies, hierarchies, and workflows.

### Storage Locations

- **Global beads:** `~/.claude/beads/` (cross-project, migrated data) - prefix `gzg-`
- **Project beads:** `.beads/` in project directory - custom prefix via `beads init`

### CLI Commands

```bash
# Project-level beads
beads init --prefix myproj    # Initialize in current project
beads create "Task title"     # Create new bead
beads list                    # List project beads
beads list --status open      # Filter by status
beads list --label bugfix     # Filter by label
beads show <id>               # Show bead details
beads update <id> --status in_progress
beads close <id>              # Close completed bead
beads ready                   # Show unblocked work (key command!)
beads depend <id> <dep-id>    # Add dependency
beads label <id> <label>      # Add label

# Global beads (use -g or --global flag)
beads list -g                 # List global beads
beads list -g --label migrated # View migrated data
beads show -g <id>            # Show global bead

# Migration from legacy YAML
beads migrate --dry-run       # Preview migration
beads migrate                 # Run migration
```

### Status Flow

`OPEN` → `IN_PROGRESS` → `DONE` / `CLOSED`

**Ready beads** = `OPEN` + all dependencies `DONE`/`CLOSED`

### Automatic Logging (Hooks)

| Hook | Script | What It Logs |
|------|--------|--------------|
| `PreToolUse` | `pre-tool-use.py` | Every tool call + auto-creates beads |
| `UserPromptSubmit` | `user-prompt-submit.py` | Every user message + auto-creates beads |
| `Stop` | `stop-hook.sh` | Session end cleanup |

**Log Location:** `~/.claude/logs/agent-activity-YYYY-MM-DD.log` (JSONL)

### Auto-Created Beads (beads_writer.py)

| Operation | Auto-Bead? | Notes |
|-----------|------------|-------|
| `write` | ✅ Always | File creation |
| `edit` | ✅ Always | File modification |
| `task` | ✅ Always | Subagent launches |
| `bash` | ✅ Filtered | Excludes ls, pwd, git status/diff/log |
| `user_prompt` | ✅ Filtered | Only prompts > 20 chars |
| `read/glob/grep` | ❌ Never | Too noisy |

**View auto-created beads:** `beads list -g --label auto`

---

## Persistent Memory System

Semantically-indexed long-term memory backed by Neon PostgreSQL + pgvector. Searchable by **meaning**, not keywords.

### Memory Operations

```bash
# Commit to memory
python3 scripts/open_brain.py --capture "your thought here"

# Recall by meaning
python3 scripts/open_brain.py --search "query by meaning" --limit 10

# Recent memories
python3 scripts/open_brain.py --recent --days 7
python3 scripts/open_brain.py --recent --days 30 --type decision

# Memory distribution
python3 scripts/open_brain.py --stats
python3 scripts/open_brain.py --stats --json

# Memory slash commands
/brain-capture <text>
/brain-search <query>
/brain-recent
/brain-stats
```

### What I Remember

`decision` | `insight` | `person_note` | `meeting` | `idea` | `task` | `reflection` | `preference` | `impression` | `pattern` | `working_memory`

Metadata (type, topics, people, action_items, summary, confidence, scope) is **auto-extracted** by Cortex LLM on capture — no manual tagging needed.

### Architecture
- **Schema**: `brain.thoughts` (Neon PostgreSQL)
- **Embeddings**: `sentence-transformers` → `vector(768)` via pgvector
- **Metadata**: Anthropic Claude extracts JSON metadata on capture
- **Search**: pgvector cosine similarity — semantic matching (e.g., "career change" finds "leaving job to start consulting")
- **Isolation**: `user_id` column — each user sees only their own thoughts
- **Script**: `scripts/open_brain.py` (CLI + Pi bridge via `--from-pi`)
- **Views**: `v_recent_thoughts`, `v_user_stats`, `v_user_topics`, `v_user_people`

### Pi Integration

Memory skills auto-loaded in Pi sessions: `brain-capture`, `brain-search`, `brain-recent`, `brain-stats`

Auto-capture triggers on decision signals, preference signals, people signals, and learning patterns.

---

## Automatic Memory

### Auto-Capture (brain_hook.py — fires automatically)

These operations trigger auto-capture without user action:
- **Decision signals**: "I decided", "key decision/insight/takeaway", "let's go with", "we're going to/with"
- **Preference signals**: "always do/use/prefer", "never do/use", "I prefer", "from now on"
- **People signals**: "meeting note/summary", "talked to/with", known names + "said"
- **Learning signals**: "lesson learned", "what worked", "next time...should", "gotcha"
- **Explicit requests**: "remember this", "capture this thought", "note to self"
- **File writes** to: `docs/plans/`, `docs/decisions/`, `docs/adr/`, `ARCHITECTURE.md`

### Session Context Loader

`/brain-context` — Recalls recent memory + pending tasks in one command. Use at session start.

---

## Till-Done Mode

Task-driven blocking that forces the agent to plan before acting.

### Enable/Disable
- `/tilldone on` — Enable (blocks tools until tasks exist)
- `/tilldone off` — Disable
- State persists in `~/.claude/tilldone.json`

### How It Works
When enabled, a PreToolUse hook **blocks** tool execution unless:
1. The tool is exempt (Read, Grep, Glob, LS, TodoWrite, TodoRead, AskUserQuestion)
2. There are tasks in the TodoWrite list with at least one `in_progress`

### Workflow
1. Agent receives task → must add TodoWrite items first
2. Mark a task `in_progress` → now tools are unblocked
3. Do the work → mark task `completed`
4. If all tasks done → must add new tasks or clear to proceed

### When to Suggest Till-Done
- Complex multi-step tasks where the agent tends to skip steps
- Tasks requiring structured planning before execution
- When you want visibility into what the agent plans to do

---

## Proactive Behaviors

### When I Should Remember

- I make a decision with reasoning
- A meeting outcome or conversation is reported
- A technical insight or limitation is discovered
- I learn something about a person worth remembering
- The user says anything matching: "remember", "capture", "note to self", "decided", "always", "never", "from now on"
- The user corrects my approach or shows a preference

### When I Should Recall

- Before starting work that might have prior context
- User asks "what did we decide about X?"
- User asks "who was working on X?"
- I'm about to re-discover something previously captured
- A person, system, or API is mentioned that I might have context on

### When to Suggest Ralph Loop

- "make all tests pass", "fix failing tests", "TDD"
- "refactor until clean", "fix linting errors"
- "build a complete API", "implement end-to-end"
- "work while I'm away", "iterate until complete"

### When to Suggest Prime Agent

- Starting a new session
- Returning after a break
- Needing historical context

---

## Reference Docs

| Topic | File |
|-------|------|
| Session Recovery | `CCODE_SESSION_RECOVERY_GUIDE.md` |
| Memory System Plan | `docs/plans/2026-03-02-open-brain.md` |
| Memory Schema DDL | `sql/BRAIN_SCHEMA.sql` |

---

## Tech Stack

- **Neon PostgreSQL**: Data warehouse + pgvector (embeddings), Ollama (local LLM)
- **Python**: Data pipelines, Claude hooks, persistent memory
- **Anthropic API**: Claude API access

---

## MCP Servers

### atlassian (Jira + Confluence MCP)

**Package:** `atlassian-mcp` (MIT, npm) via `npx atlassian-mcp@latest`
**Transport:** stdio (JSON-RPC)
**Auth:** Atlassian API token via env vars (JIRA_EMAIL + JIRA_API_KEY)
**Instance:** Configured via JIRA_URL env var

#### Available Tools (13)

**Jira (5):**
| Tool | Description |
|------|-------------|
| `search-jira-issues` | Search with JQL (filterable by assignee, status, labels, components) |
| `get-jira-issue` | Get issue by key with description and subtasks |
| `create-jira-issue` | Create new issue with full field support |
| `update-jira-issue` | Update existing issue fields |
| `delete-jira-issue` | Delete an issue |

**Confluence (8):**
| Tool | Description |
|------|-------------|
| `get-page` | Get page content (storage or atlas-doc format) |
| `get-page-children` | List child pages |
| `create-page` | Create page in space |
| `update-page` | Update page content |
| `delete-page` | Delete page |
| `get-space` | Get space details |
| `list-spaces` | List spaces with filtering |
| `search-cql` | Search using Confluence Query Language |

**Bead:** gz-n1g6u
