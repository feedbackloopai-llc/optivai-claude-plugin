# Claude Code Activity Logging System

## Overview

This document provides detailed technical documentation for the Claude Code activity logging system, which captures AI assistant operations and syncs them to PostgreSQL.

## System Components

### 1. Hook Scripts

Claude Code supports hooks that execute on specific events. This plugin uses three hooks:

#### PreToolUse Hook (`pre_tool_use.py`)

**Trigger:** Before Claude Code executes any tool (Read, Write, Bash, Grep, etc.)

**Captures:**
- Tool name (operation type)
- Tool parameters/description
- Timestamp
- Session ID
- Project context
- AWS Bedrock metadata (if applicable)

**Example log entry:**
```json
{
  "epoch": 1733847600,
  "timestamp": "2024-12-10T16:00:00.000000+00:00",
  "operation": "read",
  "prompt": "Read file: src/main.py",
  "session_id": "session-20241210-160000-abc12345",
  "agent_id": "my-project/crew/chris-hughes",
  "user": "chris-hughes",
  "project": "my-project",
  "cwd": "/Users/chris/projects/my-project",
  "provider": {
    "type": "teams",
    "model": "claude-opus-4-6",
    "organization": "FeedbackLoopAI",
    "user_email": "user@feedbackloopai.com"
  },
  "details": {
    "tool_name": "Read",
    "file_path": "src/main.py"
  }
}
```

**Agent Identity:**
The `agent_id` field follows the pattern `{project}/{role}/{user}` (Gastown BD_ACTOR pattern). Override with `CLAUDE_AGENT_ID` environment variable.

#### UserPromptSubmit Hook (`user_prompt_submit.py`)

**Trigger:** When user submits a prompt to Claude Code

**Captures:**
- Full prompt text (truncated if over limit)
- Prompt length
- Interaction type
- Timestamp
- Session context

**Example log entry:**
```json
{
  "epoch": 1733847590,
  "timestamp": "2024-12-10T15:59:50.000000+00:00",
  "operation": "user_prompt",
  "prompt": "Please read the main.py file and explain what it does",
  "session_id": "session-20241210-160000-abc12345",
  "agent_id": "my-project/crew/chris-hughes",
  "user": "chris-hughes",
  "project": "my-project",
  "details": {
    "prompt_length": 52,
    "truncated": false,
    "interaction_type": "user_input"
  }
}
```

#### Stop Hook (`session_summary.py`)

**Trigger:** When a Claude Code session ends (Stop event)

**Captures:**
- Total token usage per session (input, output, cache write, cache read)
- API-equivalent cost estimate based on model-specific pricing
- Session duration (from first to last transcript entry)
- Models used during the session
- API call count

**Example log entry:**
```json
{
  "operation": "session_summary",
  "prompt": "Session summary: 1,234,567 total tokens (in:100,000 out:50,000 cache_w:800,000 cache_r:284,567), 42 API calls, $5.23 API-equiv cost",
  "session_id": "session-20260218-143000-abc12345",
  "provider": {
    "type": "teams",
    "model": "claude-opus-4-6",
    "organization": "FeedbackLoopAI"
  },
  "details": {
    "input_tokens": 100000,
    "output_tokens": 50000,
    "cache_creation_input_tokens": 800000,
    "cache_read_input_tokens": 284567,
    "total_tokens": 1234567,
    "api_calls": 42,
    "model": "claude-opus-4-6",
    "models_used": ["claude-opus-4-6"],
    "estimated_cost_usd": 5.23,
    "session_duration_seconds": 3600
  }
}
```

**Cost Estimation:** Uses model-specific pricing with cache token rates (cache write = 1.25x input, cache read = 0.10x input). For Teams/Enterprise subscriptions, costs represent API-equivalent values, not actual billing.

### 2. Log Writer (`log_writer.py`)

Core module that handles:
- Log file management (daily rotation)
- JSON serialization
- Session ID generation
- Provider metadata extraction (Teams, Bedrock, Direct)
- Error handling and recovery

**Key Functions:**

```python
def get_log_file_path(project_path: str) -> Path:
    """Get today's log file path for a project"""

def generate_session_id() -> str:
    """Generate unique session identifier"""

def write_log_entry(entry: dict, project_path: str) -> bool:
    """Write a log entry to the appropriate log file"""

def get_provider_env() -> dict:
    """Detect provider environment (Teams, Bedrock, or Direct API)"""
```

### 3. PostgreSQL Sync Daemon (`pg_sync.py`)

Background service that syncs local logs to PostgreSQL.

**Operating Modes:**

| Mode | Command | Description |
|------|---------|-------------|
| Continuous | `--daemon` | Background process, syncs every N seconds |
| Single Pass | `--once` | One sync pass, then exit |
| Dry Run | `--dry-run` | Preview without writing |
| Status | `--status` | Show sync state |

**Sync Process:**

1. **Scan** - Find projects with `.claude/logs/` directories
2. **Read** - Load new entries since last sync position
3. **Transform** - Map to The Well schema
4. **Write** - Insert to PostgreSQL RAW_EVENTS table
5. **Checkpoint** - Save sync position for next run

**State Management:**

Sync state stored in `~/.claude/logs/.sync_state.json`:

```json
{
  "projects": {
    "/Users/chris/projects/my-project": {
      "last_file": "/Users/chris/projects/my-project/.claude/logs/agent-activity-2024-12-10.log",
      "last_position": 15234,
      "last_sync": "2024-12-10T16:05:00Z"
    }
  },
  "last_sync_time": "2024-12-10T16:05:00Z",
  "total_synced": 1523
}
```

## Data Flow

```
┌─────────────────┐
│  Claude Code    │
│  Tool Execution │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  PreToolUse     │
│  Hook Script    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  log_writer.py  │
│  - Validate     │
│  - Enrich       │
│  - Serialize    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Local Log File │
│  (JSONL format) │
└────────┬────────┘
         │
         ▼  (async, every 60s)
┌─────────────────┐
│ pg_sync  │
│  - Read new     │
│  - Transform    │
│  - Batch insert │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   PostgreSQL    │
│   RAW_EVENTS    │
└─────────────────┘
```

## The Well Schema Mapping

Local log entries are transformed to The Well schema:

| Local Field | The Well Column | Transformation |
|-------------|-----------------|----------------|
| `operation` | `EVENT_TYPE` | `BOT.TOOL.{OPERATION}` uppercase |
| `timestamp` | `EVENT_AT` | Direct mapping |
| `user` | `ACTOR_ID` | `claude-code-{user}` |
| `project` | `SUBJECT_ID` | Direct mapping |
| `agent_id` | `METADATA.agent_id` | Included in METADATA JSON |
| `*` | `METADATA` | Full entry as JSON |
| (generated) | `EVENT_ID` | `cc-{epoch}-{session_suffix}` |
| (generated) | `EVENT_NK_HASH` | SHA256 of natural key |

**Event Type Mapping:**

| Local Operation | The Well Event Type |
|-----------------|---------------------|
| `user_prompt` | `USER.PROMPT.USER_PROMPT` |
| `read` | `BOT.TOOL.READ` |
| `write` | `BOT.TOOL.WRITE` |
| `edit` | `BOT.TOOL.EDIT` |
| `bash` | `BOT.TOOL.BASH` |
| `glob` | `BOT.TOOL.GLOB` |
| `grep` | `BOT.TOOL.GREP` |
| `task` | `BOT.TOOL.TASK` |
| `todo_write` | `BOT.TOOL.TODO_WRITE` |
| `session_summary` | `SESSION.SUMMARY` |

## Configuration

### Configuration File Location

`~/.claude/hooks/auto-logger-config.json`

### Full Configuration Schema

```json
{
  "logging": {
    "enabled": true,
    "log_dir": "~/.claude/logs",
    "log_tool_operations": true,
    "log_user_prompts": true,
    "max_prompt_length": 500,
    "session_tracking": true,
    "log_directory_mode": "per_project"
  },
  "destinations": {
    "postgresql": {
      "enabled": true,
      "account": "ACCOUNT.REGION",
      "warehouse": "WAREHOUSE_NAME",
      "role": "ROLE_NAME",
      "target_table": "DATABASE.SCHEMA.RAW_EVENTS",
      "tenant_id": "CLAUDE_CODE",
      "source_system": "CLAUDE_CODE",
      "auth": {
        "type": "key_pair",
        "user": "USERNAME",
        "private_key_path": "~/.postgresql/key.p8",
        "private_key_passphrase_env": "PG_KEY_PASSPHRASE"
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

## Error Handling

### Hook Errors

Errors in hook scripts are logged to `{project}/.claude/logs/hook_errors.log` and do not block Claude Code operation.

### Sync Errors

The sync daemon implements retry logic:

1. **Transient errors** (network, timeout) - Retry with exponential backoff
2. **Permanent errors** (auth failure) - Log and skip, continue with other projects
3. **Batch failures** - Checkpoint at last successful position

### Recovery

If sync daemon crashes:
1. State file preserves last sync position
2. Restart daemon: `python3 pg_sync.py --daemon`
3. Sync resumes from checkpoint (no duplicate events due to EVENT_NK_HASH)

## Performance Considerations

### Local Logging

- **Latency:** <1ms per log entry
- **Storage:** ~200 bytes per entry average
- **Daily rotation:** One file per day per project

### PostgreSQL Sync

- **Batch size:** 100 events (configurable)
- **Sync interval:** 60 seconds (configurable)
- **Insert method:** Single-row inserts (PARSE_JSON compatibility)

### Optimization Tips

1. Increase `batch_size` for high-volume projects
2. Increase `flush_interval_seconds` to reduce PostgreSQL compute
3. Use project-specific log directories to isolate sync state

## Security

### Credentials

- PostgreSQL credentials stored in config file (gitignored)
- Private key file permissions should be `600`
- No credentials in log files

### Data Privacy

- Prompts can be truncated via `max_prompt_length`
- File contents are NOT logged (only file paths)
- Sensitive data in prompts should be handled with care

### Audit

- All events have `EVENT_NK_HASH` for integrity verification
- `INGESTED_AT` timestamp shows when synced to PostgreSQL
- Session IDs correlate related activities

## Monitoring

### Check Local Logging

```bash
# View today's log
cat ~/.claude/logs/agent-activity-$(date +%Y-%m-%d).log | jq .

# Count entries
wc -l ~/.claude/logs/agent-activity-$(date +%Y-%m-%d).log

# Check for errors
cat ~/.claude/logs/hook_errors.log
```

### Check Sync Status

```bash
# View sync state
python3 scripts/pg_sync.py --status

# View daemon logs
tail -f ~/.claude/logs/sync_daemon.log
```

### Query PostgreSQL

```sql
-- Recent sync activity
SELECT COUNT(*), MAX(INGESTED_AT)
FROM YOUR_DW_SCHEMA.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE';

-- Events per hour
SELECT
    DATE_TRUNC('hour', EVENT_AT) as hour,
    COUNT(*) as events
FROM YOUR_DW_SCHEMA.LANDING.RAW_EVENTS
WHERE SOURCE_SYSTEM = 'CLAUDE_CODE'
  AND EVENT_AT > DATEADD(day, -1, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 1;
```

## Extending the System

### Adding New Event Types

1. Add handling in `pre_tool_use.py` for the new tool
2. Map operation to event type in `pg_sync.py`
3. Update documentation

### Custom Metadata

Add fields to the `details` object in hook scripts:

```python
entry["details"]["custom_field"] = "custom_value"
```

These flow through to the `METADATA` column in PostgreSQL.

### Alternative Destinations

The sync daemon can be extended to support additional destinations by implementing the writer interface pattern used by `PostgreSQLWriter`.
