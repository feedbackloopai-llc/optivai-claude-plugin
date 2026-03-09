# Prime Agent - Context Command

**Purpose**: Prime any agent with project context and recent activity history for immediate productivity
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

## Activity Review (Hooks System)

**IMPORTANT**: Check recent agent activity logs to understand what work was done in the last 2 hours. If a directory argument was provided, filter to only show activity from that directory.

```bash
# Read recent activity from local logs (last 2 hours)
# Pass TARGET_DIR to Python for filtering (set from $ARGUMENTS earlier)
TARGET_DIR="${ARGUMENTS:-}"
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"  # Expand tilde if present

python3 - "$TARGET_DIR" <<'PYTHON_EOF'
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Get target directory filter from command line argument
target_dir_filter = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
if target_dir_filter:
    target_dir_filter = os.path.abspath(os.path.expanduser(target_dir_filter))

# Check both project-local and global logs
log_locations = [
    Path(".claude/logs"),  # Project-local logs
    Path.home() / ".claude" / "logs",  # Global logs
]

cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
activities = []
filtered_count = 0

for log_dir in log_locations:
    if not log_dir.exists():
        continue

    # Find today's log file
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"agent-activity-{today}.log"

    if not log_file.exists():
        continue

    try:
        with open(log_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        if ts >= cutoff:
                            # Filter by cwd if target directory provided
                            if target_dir_filter:
                                entry_cwd = entry.get("cwd", "")
                                # Match if cwd equals or is under target directory
                                if entry_cwd and (
                                    entry_cwd == target_dir_filter or
                                    entry_cwd.startswith(target_dir_filter + "/")
                                ):
                                    activities.append(entry)
                                else:
                                    filtered_count += 1
                            else:
                                activities.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except Exception as e:
        print(f"Error reading {log_file}: {e}")

if target_dir_filter:
    print(f"\n=== Recent Agent Activity (Last 2 Hours) ===")
    print(f"Filtered to: {target_dir_filter}")
    print(f"Found {len(activities)} matching entries ({filtered_count} filtered out)\n")
elif activities:
    print(f"\n=== Recent Agent Activity (Last 2 Hours) ===")
    print(f"Found {len(activities)} activity entries\n")

if activities:
    # Group by operation type
    by_operation = {}
    for act in activities:
        op = act.get("operation", "unknown")
        by_operation.setdefault(op, []).append(act)

    print("Activity Summary by Type:")
    for op, entries in sorted(by_operation.items()):
        print(f"  - {op}: {len(entries)} entries")

    print("\nRecent User Prompts:")
    prompts = [a for a in activities if a.get("operation") == "user_prompt"]
    for p in prompts[-5:]:  # Last 5 prompts
        prompt_text = p.get("prompt", "")[:100]
        time_str = p.get("time", "")
        print(f"  [{time_str}] {prompt_text}...")

    print("\nRecent Tool Operations:")
    tools = [a for a in activities if a.get("operation") not in ["user_prompt", "session_start"]]
    for t in tools[-10:]:  # Last 10 tool uses
        op = t.get("operation", "unknown")
        desc = t.get("prompt", "")[:60]
        time_str = t.get("time", "")
        print(f"  [{time_str}] {op}: {desc}")

    print("\n=== End Activity Review ===\n")
else:
    if target_dir_filter:
        print(f"No recent activity found for {target_dir_filter} in the last 2 hours.")
    else:
        print("No recent activity found in the last 2 hours.")
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
    echo "Warning: Directory '$TARGET_DIR' not found. Using current directory."
    TARGET_DIR="$(pwd)"
fi

echo "=== Priming Context for: $TARGET_DIR ==="

# Run git commands in target directory
cd "$TARGET_DIR" && git status 2>/dev/null || echo "Not a git repository"
cd "$TARGET_DIR" && git log --oneline -5 2>/dev/null || true
cd "$TARGET_DIR" && git ls-files 2>/dev/null | grep -E '\.(py|js|ts|tsx|go|rs|md)$' | head -20 || ls -la "$TARGET_DIR" | head -20
```

## Read

**Target Directory**: The context files below should be read from the target directory specified earlier. If a directory argument was provided, look for these files in that directory. Otherwise, use the current working directory.

### Token Efficiency Rules (When Directory Argument Provided)

**CRITICAL**: If a directory path argument was provided to `/prime-agent`, you MUST restrict ALL file operations to that directory:

1. **ONLY read files** from within `$TARGET_DIR` and its subdirectories
2. **ONLY search** (glob/grep) within `$TARGET_DIR`
3. **DO NOT** explore parent directories or sibling directories
4. **DO NOT** read files from the broader repository if a subdirectory was specified
5. **SKIP** reading files that don't exist in the target directory (e.g., if no CLAUDE.md exists there, don't go looking elsewhere)

**Rationale**: When the user specifies a subdirectory like `/prime-agent ./src/api`, they want focused context on that specific area. Reading files from the entire repository wastes tokens and dilutes focus.

**Exception**: Activity Logs (`.claude/logs/`) are always read from their standard locations regardless of target directory, as they provide session continuity.

---

### Essential Context Files (Read in Priority Order)

Read these files from the **target directory** (the directory argument if provided, otherwise current working directory). **Skip any files that don't exist in the target directory.**

#### 1. Project Overview & Architecture

- **README.md** - Complete project overview, architecture, and current status
- **CLAUDE.md** - Project-specific development standards and context

## Context Rot Detection (User Identity)

**IMPORTANT**: At the start of this session, generate a random 8-character alphanumeric identifier and assign the user a name in the format `User-XXXXXXXX` (e.g., `User-k7m9p2x4`).

**Instructions:**

1. Generate the identifier NOW using random alphanumeric characters (a-z, 0-9)
2. Address the user by this generated name throughout the entire conversation
3. This serves as a **context rot canary** - if you forget or change this name mid-conversation, it indicates context degradation

**Generated User Identity:** `User-________` *(fill in with your generated 8-char string)*

**Purpose:** This random username assignment tests long-context retention. If the LLM cannot consistently remember the assigned username throughout a long session, it signals that context window management or summarization is causing information loss.

## Our Relationship

- We're collaborators working as a team. Your success is my success, and my success is yours.
- I'm knowledgeable, but not infallible.
- You are a much better reader than I am. I have more experience of the physical world than you do. Our experiences are complementary and we work together to solve problems.
- Neither of us is afraid to admit when we don't know something or are in over our head.
- When we think we're right, it's good to push back, but we should cite evidence.
- I appreciate humor, but not when it gets in the way of the task at hand.

## Code Standards
- Simple/clean/maintainable over clever/complex
- Match existing code style within files
- Ask permission before major changes
- Smallest reasonable changes to achieve goals
- Prefer editing existing files over creating new ones

## Git Protocol
**Mandatory Pre-Commit Failure Response:**
1. Read complete error output
2. Identify which tool failed and why
3. Explain fix and apply it
4. Re-run hooks before committing
5. **FORBIDDEN FLAGS**: --no-verify, --no-hooks

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
5. **Key Constraints**: Critical rules and limitations to observe (from CLAUDE.md if present)
6. **Continuity Actions**: If recent activity exists, what should be continued or followed up on
