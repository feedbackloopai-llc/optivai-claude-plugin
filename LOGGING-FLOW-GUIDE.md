# Automatic Activity Logging - Flow Guide

## Where Files Are Logged

### Log File Location

**Path:** `.claude/logs/agent-activity-YYYY-MM-DD.log`

**Example:**
```
.claude/logs/agent-activity-2025-12-09.log
```

**Structure:**
```
project-directory/
└── .claude/
    ├── hooks/
    │   ├── pre-tool-use.py          # Hook that captures tool operations
    │   ├── user-prompt-submit.py    # Hook that captures user prompts
    │   ├── log-writer.py            # Core logger (writes to logs)
    │   └── auto-logger-config.json  # Configuration
    ├── logs/                         # ← LOG FILES GO HERE
    │   ├── agent-activity-2025-12-09.log  # Daily log file
    │   └── hook_errors.log               # Error log (if any)
    └── settings.local.json          # Hooks configuration
```

---

## How Logging Works

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. USER SUBMITS PROMPT                                          │
│    "Read the README.md file"                                    │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. UserPromptSubmit HOOK TRIGGERS                               │
│    Claude Code executes:                                        │
│    python .claude/hooks/user-prompt-submit.py                   │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. USER PROMPT LOGGED                                           │
│    log_writer.py writes to:                                     │
│    .claude/logs/agent-activity-2025-12-09.log                   │
│                                                                  │
│    {"timestamp":"2025-12-09T20:35:00Z",                         │
│     "operation":"user_prompt",                                  │
│     "prompt":"Read the README.md file",                         │
│     "session_id":"session-20251209-203500-a1b2c3d4"}           │
└─────────────────────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. CLAUDE USES TOOL (Read)                                      │
│    Tool: Read                                                   │
│    Input: {file_path: "README.md"}                              │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. PreToolUse HOOK TRIGGERS                                     │
│    Claude Code executes:                                        │
│    python .claude/hooks/pre-tool-use.py                         │
│                                                                  │
│    Hook receives via stdin:                                     │
│    {"tool_name":"Read", "tool_input":{"file_path":"README.md"}} │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. TOOL OPERATION LOGGED                                        │
│    log_writer.py appends to:                                    │
│    .claude/logs/agent-activity-2025-12-09.log                   │
│                                                                  │
│    {"timestamp":"2025-12-09T20:35:01Z",                         │
│     "operation":"read",                                         │
│     "prompt":"read: README.md",                                 │
│     "session_id":"session-20251209-203500-a1b2c3d4"}           │
└─────────────────────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7. TOOL EXECUTES                                                │
│    Read tool reads README.md                                    │
│    Returns content to Claude                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## What Gets Logged

### Tool Operations Captured

All Claude Code tool operations are logged:

| Tool | Operation | Example Prompt |
|------|-----------|----------------|
| **Read** | `read` | `"read: src/app.ts"` |
| **Write** | `write` | `"write: config.json"` |
| **Edit** | `edit` | `"edit: package.json"` |
| **Bash** | `bash` | `"bash: git status"` |
| **Glob** | `glob` | `"glob: **/*.ts"` |
| **Grep** | `grep` | `"grep: function.*export"` |
| **Task** | `task` | `"task: code-quality-reviewer"` |
| **WebFetch** | `web_fetch` | `"web_fetch: https://example.com"` |
| **WebSearch** | `web_search` | `"web_search: claude code hooks"` |
| **TodoWrite** | `todo_write` | `"todo_write: updating task list"` |
| **SlashCommand** | `slash_command` | `"slash_command: /view-logs"` |

### User Prompts Captured

Every user input is logged:

```json
{
  "operation": "user_prompt",
  "prompt": "Help me implement a new feature",
  "session_id": "session-20251209-203500-a1b2c3d4"
}
```

---

## Log File Format

### JSON Lines Format

Each line in the log file is a complete JSON object:

```json
{"timestamp":"2025-12-09T20:35:00.123Z","operation":"user_prompt","prompt":"Read the README file","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:00"}
{"timestamp":"2025-12-09T20:35:01.456Z","operation":"read","prompt":"read: README.md","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:01"}
{"timestamp":"2025-12-09T20:35:05.789Z","operation":"bash","prompt":"bash: git status","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:05"}
```

### Log Entry Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `timestamp` | ISO 8601 | UTC timestamp | `"2025-12-09T20:35:00.123Z"` |
| `operation` | string | Type of operation | `"read"`, `"write"`, `"bash"` |
| `prompt` | string | Human-readable description | `"read: README.md"` |
| `session_id` | string | Unique session identifier | `"session-20251209-203500-a1b2c3d4"` |
| `time` | string | Local time (HH:MM:SS) | `"20:35:00"` |
| `result` | string | Optional result/output | `"File read successfully"` |
| `context` | object | Optional additional metadata | `{"file": "README.md"}` |

---

## How to View Logs

### Method 1: Use the Command (Recommended)

```bash
# View last 20 entries (default)
/view-logs

# View last 50 entries
/view-logs 50

# View all today's logs
/view-logs today
```

### Method 2: Direct File Access

```bash
# View today's log file
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log

# View with pretty printing
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq .

# Count total entries
wc -l .claude/logs/agent-activity-$(date +%Y-%m-%d).log

# Search for specific operations
grep '"operation":"read"' .claude/logs/agent-activity-$(date +%Y-%m-%d).log

# Get unique sessions
cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq -r '.session_id' | sort -u
```

### Method 3: Export for Analysis

```bash
# Export as JSON (machine-readable)
/export-logs json

# Export as CSV (spreadsheet)
/export-logs csv

# Export as Markdown (human-readable report)
/export-logs markdown
```

---

## Example: Complete Session Log

### Scenario: User asks Claude to read and update a file

**Log File:** `.claude/logs/agent-activity-2025-12-09.log`

```json
{"timestamp":"2025-12-09T20:35:00.000Z","operation":"user_prompt","prompt":"Read package.json and update the version to 2.0.0","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:00"}
{"timestamp":"2025-12-09T20:35:01.100Z","operation":"read","prompt":"read: package.json","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:01"}
{"timestamp":"2025-12-09T20:35:02.200Z","operation":"edit","prompt":"edit: package.json","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:02"}
{"timestamp":"2025-12-09T20:35:03.300Z","operation":"bash","prompt":"bash: git status","session_id":"session-20251209-203500-a1b2c3d4","time":"20:35:03"}
```

### Session Timeline

| Time | Operation | What Happened |
|------|-----------|---------------|
| 20:35:00 | user_prompt | User requested version update |
| 20:35:01 | read | Claude read package.json |
| 20:35:02 | edit | Claude edited package.json |
| 20:35:03 | bash | Claude checked git status |

---

## Session Tracking

### Session ID Format

```
session-YYYYMMDD-HHMMSS-shortid
```

**Example:** `session-20251209-203500-a1b2c3d4`

**Parts:**
- `session-` - Prefix
- `20251209` - Date (YYYY-MM-DD)
- `203500` - Time (HH:MM:SS)
- `a1b2c3d4` - Short UUID (8 characters)

### Session Metadata

**File:** `.claude/logs/session-20251209-203500-a1b2c3d4.metadata.json`

```json
{
  "session_id": "session-20251209-203500-a1b2c3d4",
  "start_time": "2025-12-09T20:35:00.000Z",
  "cwd": "C:\\Users\\chris\\Documents\\optivai-claude-plugin",
  "log_dir": ".claude/logs"
}
```

---

## Daily Rotation

### How It Works

1. **New Day = New Log File**
   - Each day gets its own log file
   - Format: `agent-activity-YYYY-MM-DD.log`

2. **Automatic Creation**
   - First operation of the day creates the file
   - No manual intervention needed

3. **No Automatic Deletion**
   - Old logs are never automatically deleted
   - You control retention manually

### Example Log Files

```
.claude/logs/
├── agent-activity-2025-12-09.log  # Today
├── agent-activity-2025-12-08.log  # Yesterday
├── agent-activity-2025-12-07.log  # 2 days ago
└── hook_errors.log                # Errors (if any)
```

---

## Privacy & Security

### What Is NOT Logged

- ❌ File contents (only file paths)
- ❌ Bash command output (only commands)
- ❌ API responses (only that API was called)
- ❌ Passwords or secrets (can be filtered)

### What IS Logged

- ✅ Tool names (Read, Write, Edit, etc.)
- ✅ File paths being accessed
- ✅ Bash commands executed
- ✅ User prompts (truncated to 500 chars)
- ✅ Session IDs and timestamps

### Sensitive File Exclusion

Edit `.claude/hooks/auto-logger-config.json`:

```json
{
  "ignore_patterns": [
    "*.log",
    ".env",
    ".env.*",
    "secrets/*",
    "credentials/*",
    "*.key",
    "*.pem"
  ]
}
```

---

## Troubleshooting

### No Logs Appearing?

**Check if logging is enabled:**
```bash
cat .claude/hooks/auto-logger-config.json | grep enabled
```

**Check if hooks are configured:**
```bash
cat .claude/settings.local.json | grep -A 10 hooks
```

**Check for errors:**
```bash
cat .claude/logs/hook_errors.log
```

### Hook Not Triggering?

**Verify hook scripts exist:**
```bash
ls -lh .claude/hooks/
```

**Check permissions:**
```bash
chmod +x .claude/hooks/*.py
```

**Test manually:**
```bash
python .claude/hooks/log-writer.py
```

---

## Performance

### Overhead Metrics

- **Hook execution time:** < 5ms per operation
- **File write time:** < 1ms (append-only)
- **Memory usage:** Minimal (no buffering)
- **CPU impact:** Negligible

### Smart Filtering

The system includes smart filtering to reduce noise:

- Rapid repeated operations are deduplicated
- Minimum interval between similar operations
- Ignore patterns exclude noisy files

---

## Summary

**Where:** `.claude/logs/agent-activity-YYYY-MM-DD.log`

**How:** Hooks capture operations → log_writer.py writes JSON Lines

**What:** All tool operations (Read, Write, Bash, etc.) + user prompts

**When:** Every Claude Code operation (< 5ms overhead)

**Format:** JSON Lines (one JSON object per line)

**View:** `/view-logs` or `cat .claude/logs/agent-activity-$(date +%Y-%m-%d).log`

**Export:** `/export-logs json|csv|markdown`
