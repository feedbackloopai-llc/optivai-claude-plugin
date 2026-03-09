Toggle till-done mode. $ARGUMENTS

Till-done forces the agent to manage a task list before executing tools.

To enable:
```bash
python3 -c "
import json; from pathlib import Path
p = Path.home() / '.claude' / 'tilldone.json'
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({'enabled': True, 'tasks': []}, indent=2))
print('✅ Till-Done mode ENABLED — create tasks via TodoWrite before using other tools')
"
```

To disable:
```bash
python3 -c "
import json; from pathlib import Path
p = Path.home() / '.claude' / 'tilldone.json'
p.write_text(json.dumps({'enabled': False, 'tasks': []}, indent=2))
print('❌ Till-Done mode DISABLED')
"
```

To check status:
```bash
cat ~/.claude/tilldone.json 2>/dev/null || echo '{"enabled": false}'
```

If $ARGUMENTS contains "on" or "enable", enable it.
If $ARGUMENTS contains "off" or "disable", disable it.
If $ARGUMENTS is empty, toggle (check current state and flip).

When enabled, remind the user:
- TodoWrite/TodoRead, Read, Grep, Glob are always allowed
- All other tools require an active task (in_progress status)
- Workflow: add tasks → mark in_progress → do work → mark completed
