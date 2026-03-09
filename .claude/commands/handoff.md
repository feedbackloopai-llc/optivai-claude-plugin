# Handoff - Session Continuity Transfer

**Purpose**: Prepare structured handoff context for session continuity using Beads
**Usage**: `/handoff [optional: summary of current state]`

## When to Use

- **End of work session** - Before stopping work for the day
- **Context getting full** - When approaching token limits
- **Before context switch** - When switching to a different project
- **Recovery preparation** - Before risky operations

## Execute

```bash
python3 - "$ARGUMENTS" <<'PYTHON_EOF'
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

user_summary = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None

# Gather context from JSONL hook logs (last session's activity)
log_dir = Path.home() / ".claude" / "logs"
recent_work = []
session_id = "unknown"
project = os.path.basename(os.getcwd())

if log_dir.exists():
    log_files = sorted(log_dir.glob("agent-activity-*.log"))[-2:]  # Last 2 days
    for log_file in log_files:
        try:
            with open(log_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            recent_work.append(entry)
                            if entry.get('session_id'):
                                session_id = entry['session_id']
                            if entry.get('project'):
                                project = entry['project']
                        except:
                            pass
        except:
            pass

# Build handoff description
parts = []
if user_summary:
    parts.append(f"Focus: {user_summary}")
parts.append(f"Project: {project}")
parts.append(f"Session: {session_id}")
parts.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# Add recent significant operations
significant_ops = [w for w in recent_work[-20:] if w.get('operation') in ('write', 'edit', 'bash', 'task')]
if significant_ops:
    parts.append("Recent work:")
    for op in significant_ops[-5:]:
        desc = op.get('prompt', op.get('details', {}).get('tool_name', ''))[:60]
        parts.append(f"  - {op.get('operation', '?')}: {desc}")

# Collect in-progress beads
try:
    result = subprocess.run(
        ['beads', 'list', '-g', '--status', 'in_progress'],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0 and result.stdout.strip():
        parts.append("In-progress beads:")
        for line in result.stdout.strip().split('\n')[:5]:
            parts.append(f"  {line.strip()}")
except:
    pass

description = '\n'.join(parts)

# Create handoff bead in Beads global database
# NOTE: beads CLI 'create' has no -g/--global flag, so use Python API directly
title = f"HANDOFF: {user_summary or project} ({datetime.now().strftime('%m/%d %H:%M')})"
bead_id = "failed"
try:
    sys.path.insert(0, str(Path.home() / ".claude" / "hooks"))
    from beads_writer import _get_beads_db
    db = _get_beads_db()
    if db:
        issue = db.create(title=title, description=description, labels=["handoff"])
        bead_id = issue.id
except Exception as e:
    # Fallback: create in project beads via CLI (no -g flag)
    try:
        result = subprocess.run(
            ['beads', 'create', title, '--description', description],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            bead_id = result.stdout.strip().split()[-1] if result.stdout.strip() else "unknown"
            subprocess.run(['beads', 'label', bead_id, 'handoff'], capture_output=True, timeout=5)
    except Exception as e2:
        bead_id = f"error: {e2}"

# Output summary
print("")
print("=" * 60)
print("  HANDOFF CONTEXT PREPARED")
print("=" * 60)
print("")
print(f"Project:  {project}")
print(f"Session:  {session_id}")
print(f"Bead:     {bead_id}")
print("")

if user_summary:
    print(f"Focus: {user_summary}")
    print("")

if significant_ops:
    print("Recent Work (last 5):")
    for op in significant_ops[-5:]:
        time_str = op.get('time', op.get('timestamp', '')[:19])
        desc = op.get('prompt', op.get('details', {}).get('tool_name', ''))[:50]
        print(f"  [{time_str}] {op.get('operation', '?')}: {desc}")
    print("")

print("=" * 60)
print(f"Handoff saved as Bead: {bead_id}")
print("")
print("To resume, the new session should run: /prime-agent")
print("=" * 60)
PYTHON_EOF
```

## Output

Creates a Bead with label `handoff` containing:
- Current project and session context
- What was being worked on
- Recent significant operations
- In-progress beads from current session

The new session uses `/prime-agent` to pick up this context automatically via `beads ready`.
