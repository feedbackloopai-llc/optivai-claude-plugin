# Quick Context - Fast Context Loading

**Purpose**: Fast context loading for experienced agents
**Usage**: `/quick-context`

## Quick Prime

Read the README.md and CLAUDE.md files in the current directory to understand the project context. Check recent activity logs for continuity.

```bash
# Show recent activity summary
python3 <<'PYTHON_EOF'
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

log_dir = Path(".claude/logs")
if not log_dir.exists():
    log_dir = Path.home() / ".claude" / "logs"

today = datetime.now().strftime("%Y-%m-%d")
log_file = log_dir / f"agent-activity-{today}.log"

if log_file.exists():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    recent = []
    with open(log_file, 'r') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                ts = datetime.fromisoformat(entry.get("timestamp", "").replace('Z', '+00:00'))
                if ts >= cutoff:
                    recent.append(entry)
            except:
                pass
    if recent:
        print(f"Recent activity: {len(recent)} operations in last 2 hours")
        prompts = [e for e in recent if e.get("operation") == "user_prompt"]
        if prompts:
            print(f"Last prompt: {prompts[-1].get('prompt', '')[:80]}...")
    else:
        print("No recent activity in last 2 hours")
else:
    print("No activity logs found")
PYTHON_EOF
```

After running the above, read README.md and CLAUDE.md if they exist, then confirm you're ready to assist.
