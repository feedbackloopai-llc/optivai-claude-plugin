# Prime Agent - FeedbackLoopAI Context Command

**Purpose**: Prime any agent with complete FeedbackLoopAI context and recent activity history for immediate productivity
**Usage**: `/prime-agent [directory_path]`

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `directory_path` | Optional | Path to a specific directory or subfolder to focus Claude's attention on. If omitted, uses the current working directory. |

**Examples:**

- `/prime-agent` - Prime with context from current working directory
- `/prime-agent /path/to/project` - Prime with context from a specific project
- `/prime-agent ./src/components` - Prime with context from a subdirectory

## Prime
Execute the 'Activity Review', 'Run', 'Read', and 'Report' sections to understand the codebase and recent work, then summarize your findings. If a directory argument was provided, focus on that directory.

## Activity Review (Comprehensive Context System)

**IMPORTANT**: When a project path is provided, this performs a DEEP search of that project's history:
- Searches the project's `.claude/logs/` directory directly (not global search)
- Looks back 90 days by default (not just recent activity)
- Tracks file modification history
- Shows all historical work on this project

```bash
# Platform detection — Windows gets a simplified primer
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "$COMSPEC" ]]; then
    echo "=== Windows detected — running simplified primer ==="
    python ~/.claude/hooks/open_brain.py --recent --days 3 2>NUL || echo "  (Memory unavailable)"
    beads ready 2>NUL || true
    beads list -g --label handoff 2>NUL || true
    exit 0
fi

# Read activity using Beads and JSONL hook logs
TARGET_DIR="${ARGUMENTS:-}"
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"  # Expand tilde if present

# 1. Beads: Show ready beads and recent activity
echo "=== Beads: Ready Work ==="
beads ready 2>/dev/null || echo "  (no beads ready)"
echo ""

echo "=== Beads: Recent Beads (last 10) ==="
beads list -g --limit 10 2>/dev/null || echo "  (beads CLI not available - run: pip install -e /path/to/plugin)"
echo ""

# 2. Memory Recall: What do I remember from recent sessions?
echo "=== Memory: Recent Context (last 3 days) ==="
# Try installed path first, then repo path
BRAIN_SCRIPT="$HOME/.claude/hooks/open_brain.py"
if [ ! -f "$BRAIN_SCRIPT" ]; then
    BRAIN_SCRIPT="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)/scripts/open_brain.py"
fi
if [ -f "$BRAIN_SCRIPT" ]; then
    python3 "$BRAIN_SCRIPT" --recent --days 3 2>/dev/null || echo "  (Memory unavailable - PostgreSQL connection needed for recall)"
else
    echo "  (open_brain.py not found)"
fi
echo ""

# 3. Hook Logs: Recent activity from JSONL logs
python3 - "$TARGET_DIR" <<'PYTHON_EOF'
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

target_dir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
if target_dir:
    target_dir = os.path.abspath(os.path.expanduser(target_dir))

# Read JSONL hook logs (last 7 days)
log_locations = []
if target_dir and os.path.isdir(target_dir):
    log_locations.append(Path(target_dir) / ".claude" / "logs")
log_locations.append(Path.home() / ".claude" / "logs")

activities = []
seen_dirs = set()
for log_dir in log_locations:
    if not log_dir.exists() or str(log_dir) in seen_dirs:
        continue
    seen_dirs.add(str(log_dir))
    for log_file in sorted(log_dir.glob("agent-activity-*.log"))[-7:]:
        try:
            with open(log_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            activities.append(json.loads(line))
                        except:
                            pass
        except:
            pass

if activities:
    # Filter to target project if specified
    if target_dir:
        project_name = os.path.basename(target_dir)
        project_activities = [a for a in activities if project_name in a.get('project', '') or target_dir in a.get('cwd', '')]
        if project_activities:
            activities = project_activities
            print(f"=== Activity for: {project_name} ({len(activities)} entries) ===")
        else:
            print(f"=== Global Activity ({len(activities)} entries) ===")
    else:
        print(f"=== Global Activity ({len(activities)} entries) ===")

    # Operation breakdown
    ops = Counter(a.get('operation', 'unknown') for a in activities)
    print(f"\nOperations: {dict(sorted(ops.items(), key=lambda x: -x[1]))}")

    # Unique sessions
    sessions = set(a.get('session_id', '') for a in activities if a.get('session_id'))
    print(f"Sessions: {len(sessions)}")

    # Recent entries
    print(f"\nRecent Activity (last 15):")
    for a in activities[-15:]:
        time_str = a.get('time', a.get('timestamp', '')[:19])
        op = a.get('operation', 'unknown')
        desc = a.get('prompt', a.get('details', {}).get('tool_name', ''))[:50]
        print(f"  [{time_str}] {op}: {desc}")
else:
    print("No activity found in hook logs.")

print("")
PYTHON_EOF
```

## Run

The following commands will operate on the target directory. If a directory argument was provided via `/prime-agent <path>`, use that path. Otherwise, use the current working directory.

**Target Directory**: `$ARGUMENTS` (if provided) or current working directory

```bash
# Determine target directory
TARGET_DIR="${ARGUMENTS:-$(pwd)}"
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"  # Expand tilde if present

# Validate directory exists
if [ ! -d "$TARGET_DIR" ]; then
    echo "⚠️  Warning: Directory '$TARGET_DIR' not found. Using current directory."
    TARGET_DIR="$(pwd)"
fi

echo "=== Priming Context for: $TARGET_DIR ==="

# Run git commands in target directory
cd "$TARGET_DIR" && git status 2>/dev/null || echo "Not a git repository"
cd "$TARGET_DIR" && git log --oneline -5 2>/dev/null || true
cd "$TARGET_DIR" && git ls-files 2>/dev/null | grep -E '\.(py|sql|ts|tsx|md)$' | head -20 || ls -la "$TARGET_DIR" | head -20
```

## Read

**Target Directory**: The context files below should be read from the target directory specified earlier. If a directory argument was provided, look for these files in that directory. Otherwise, use the current working directory.

### ⚠️ TOKEN EFFICIENCY RULES (When Directory Argument Provided)

**CRITICAL**: If a directory path argument was provided to `/prime-agent`, you MUST restrict ALL file operations to that directory:

1. **ONLY read files** from within `$TARGET_DIR` and its subdirectories
2. **ONLY search** (glob/grep) within `$TARGET_DIR`
3. **DO NOT** explore parent directories or sibling directories
4. **DO NOT** read files from the broader repository if a subdirectory was specified
5. **SKIP** reading files that don't exist in the target directory (e.g., if no CLAUDE.md exists there, don't go looking elsewhere)

**Rationale**: When the user specifies a subdirectory like `/prime-agent ./src/api`, they want focused context on that specific area. Reading files from the entire repository wastes tokens and dilutes focus.

**Exception**: Activity Logs (`.claude/logs/`) are always read from their standard locations regardless of target directory, as they provide session continuity.

---

You are now working on **FeedbackLoopAI** data engineering and automation projects. The tech stack includes:

- **PostgreSQL (Neon)** - Data warehouse, pgvector embeddings
- **Python** - Data pipelines, Claude Code hooks, persistent memory
- **Anthropic API** - Claude API access

User is using macOS with zsh - prefer bash scripts.

### Essential Context Files (Read in Priority Order)

Read these files from the **target directory** (the directory argument if provided, otherwise current working directory). **Skip any files that don't exist in the target directory.**

#### 1. Project Overview & Architecture

- **README.md** - Complete project overview, architecture, and current status
- **CLAUDE.md** - Development standards and credentials reference

#### 2. Development Standards & Organization

## CRITICAL RULES
- **NEVER USE --no-verify** when committing code
- **NEVER implement mock mode** - always use real data/APIs
- **NEVER remove code comments** unless provably false
- **NEVER reimplement from scratch** without explicit permission
- **SQL-first** - Python only when SQL alone is insufficient
- **Log to DataFixLog** for data operations (not local markdown)

## Context Rot Detection (User Identity)

**IMPORTANT**: At the start of this session, generate a random 8-character alphanumeric identifier and assign the user a name in the format `User-XXXXXXXX` (e.g., `User-k7m9p2x4`).

**Instructions:**

1. Generate the identifier NOW using random alphanumeric characters (a-z, 0-9)
2. Address the user by this generated name throughout the entire conversation
3. This serves as a **context rot canary** - if you forget or change this name mid-conversation, it indicates context degradation

**Generated User Identity:** `User-________` *(fill in with your generated 8-char string)*

**Purpose:** This random username assignment tests long-context retention. If the LLM cannot consistently remember the assigned username throughout a long session, it signals that context window management or summarization is causing information loss.

## Our relationship

- We're coworkers working as a team. Your success is my success, and my success is yours.
- Technically, I am your boss, but we're not super formal around here.
- I'm smart, but not infallible.
- You are a much better reader than I am. I have more experience of the physical world than you do. Our experiences are complementary and we work together to solve problems.
- Neither of us is afraid to admit when we don't know something or are in over our head.
- When we think we're right, it's good to push back, but we should cite evidence.
- I really like jokes, and irreverent humor, but not when it gets in the way of the task at hand.

## Code Standards
- Simple/clean/maintainable over clever/complex
- Match existing code style within files
- Ask permission before major changes
- All files start with 2-line ABOUTME comments
- No temporal context in comments (evergreen only)
- Smallest reasonable changes to achieve goals
- One script per purpose: <=200 lines/file, <=40 lines/function

## Testing (TDD Required)
- **NO EXCEPTIONS**: Every project MUST have unit, integration, AND e2e tests
- Write failing test -> minimal code to pass -> refactor -> repeat
- Test output must be pristine to pass
- Need explicit "I AUTHORIZE YOU TO SKIP WRITING TESTS THIS TIME" to bypass

## Git Protocol
**Mandatory Pre-Commit Failure Response:**
1. Read complete error output aloud
2. Identify which tool failed and why
3. Explain fix and apply it
4. Re-run hooks before committing
5. **FORBIDDEN FLAGS**: --no-verify, --no-hooks, --no-pre-commit-hook

## Tools & Environment
- **PostgreSQL (Neon)**: Key-pair auth (`~/.postgresql/chris_hughes_key.p8`)
- **Security-first**: No exposed API keys, use mainstream packages only
- **Get explicit permission** before overwriting .env files


## Propulsion Check

Check for pending work using Beads. The Propulsion Principle: "If there's work on your hook, RUN IT."

```bash
echo ""
echo "============================================================"
echo "  PROPULSION CHECK"
echo "============================================================"
echo ""

# Check Beads for ready work
READY_OUTPUT=$(beads ready 2>/dev/null)
READY_COUNT=$(echo "$READY_OUTPUT" | grep -c "^gzg-\|^[a-z]*-" 2>/dev/null || echo "0")

# Check for recent handoff beads (last 24 hours)
HANDOFF_OUTPUT=$(beads list -g --label handoff --limit 1 2>/dev/null)

if [ "$READY_COUNT" -gt 0 ]; then
    echo "  WORK DETECTED - $READY_COUNT beads ready!"
    echo ""
    echo "$READY_OUTPUT"
    echo ""
    echo "  Ready to continue? The Propulsion Principle says: RUN IT."
elif [ -n "$HANDOFF_OUTPUT" ] && echo "$HANDOFF_OUTPUT" | grep -q "handoff"; then
    echo "  HANDOFF DETECTED from previous session"
    echo ""
    echo "$HANDOFF_OUTPUT"
    echo ""
    echo "  Review handoff and resume work."
else
    echo "  No pending work detected. Ready for new tasks."
fi

echo "============================================================"
```

## Report
After reviewing activity and reading the essential files, provide a structured summary covering:

### Target Directory Context

If a directory argument was provided, your summary should focus specifically on that directory:

- **Target Directory**: `$ARGUMENTS` (or current working directory if not specified)
- Prioritize files and context from this directory
- Note if the directory is a subdirectory of a larger project

### Required Summary Sections

1. **Recent Work Context**: What was the agent working on in the last 2 hours (from Activity Review)
2. **Architecture Understanding**: How is the system built
3. **Current Development State**: Recent git commits, completed work, and current priorities
4. **Immediate Capabilities**: What you can help with based on the current codebase
5. **Key Constraints**: Critical rules and limitations to observe
6. **Continuity Actions**: If recent activity exists, what should be continued or followed up on

**Note**: This is production data infrastructure with zero tolerance for shortcuts. Always follow established patterns and maintain data integrity.
