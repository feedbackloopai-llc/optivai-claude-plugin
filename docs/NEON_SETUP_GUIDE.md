# Neon PostgreSQL Setup Guide for Open Brain

This guide walks through setting up a Neon PostgreSQL database for the Open Brain semantic memory system. Each team member needs their own Neon project (free tier is sufficient).

## Prerequisites

- Python 3.10+
- An Anthropic API key (`ANTHROPIC_API_KEY` env var) for metadata extraction
- ~500MB disk space for the `all-mpnet-base-v2` embedding model (downloaded on first use)

## Step 1: Create a Neon Account

1. Go to [neon.tech](https://neon.tech) and sign up (GitHub login works)
2. Create a new project:
   - **Project name:** OpenBrain (or any name)
   - **Region:** Choose the closest AWS region to you (e.g., `us-east-1`)
   - **Postgres version:** 17 (default)
3. After creation, copy your **connection string** from the dashboard
   - Use the **direct** (non-pooler) endpoint, NOT the pooler endpoint
   - The direct endpoint looks like: `ep-abc-xyz-123456.us-east-1.aws.neon.tech`
   - The pooler adds `-pooler` to the hostname - avoid this for pgvector compatibility

### Neon Free Tier Limits
- 0.5 GB storage
- 191 compute hours/month
- Auto-suspend at idle (wakes in ~500ms on first query)
- More than enough for personal use

## Step 2: Install Python Dependencies

```bash
pip install psycopg2-binary pgvector sentence-transformers anthropic
```

On macOS with managed Python, you may need:
```bash
pip install --break-system-packages psycopg2-binary pgvector sentence-transformers anthropic
```

## Step 3: Set Environment Variables

Add to your `~/.zshrc` (or `~/.bashrc`):

```bash
# Neon PostgreSQL (OpenBrain)
export DATABASE_URL="postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"

# Anthropic API key (for metadata extraction)
export ANTHROPIC_API_KEY="sk-ant-..."
```

Replace `USER`, `PASSWORD`, `HOST`, `DBNAME` with your Neon connection details.

Then reload: `source ~/.zshrc`

## Step 4: Configure the Plugin

Edit `~/.claude/hooks/auto-logger-config.json` and add the `postgresql` destination:

```json
{
  "destinations": {
    "local": { "enabled": true },
    "postgresql": {
      "enabled": true,
      "connection_string": "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require",
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

## Step 5: Initialize the Database Schema

```bash
python3 ~/.claude/optivai-claude-plugin/scripts/open_brain.py --init
```

This creates:
- `brain` schema with `thoughts` table (pgvector-enabled, 768-dim embeddings)
- `landing` schema with `raw_events` table (activity log storage)
- Views: `v_user_stats`, `v_user_topics`, `v_user_people`

## Step 6: Verify Everything Works

```bash
# Capture a test thought
python3 ~/.claude/optivai-claude-plugin/scripts/open_brain.py --capture "Test thought about setting up Open Brain"

# Search for it
python3 ~/.claude/optivai-claude-plugin/scripts/open_brain.py --search "Open Brain setup"

# Check stats
python3 ~/.claude/optivai-claude-plugin/scripts/open_brain.py --stats

# Test the sync daemon
python3 ~/.claude/optivai-claude-plugin/scripts/pg_sync.py --once
```

## Step 7: Start the Sync Daemon (Optional)

To continuously sync Claude Code activity logs to Neon:

```bash
# Foreground (for testing)
python3 ~/.claude/optivai-claude-plugin/scripts/pg_sync.py

# Background daemon
python3 ~/.claude/optivai-claude-plugin/scripts/pg_sync.py --daemon
```

## Architecture

```
User Input (thought)
    |
    v
[sentence-transformers]     [Claude API]
  all-mpnet-base-v2          claude-haiku-4.5
  768-dim embedding           metadata extraction
    |                            |
    v                            v
[PostgreSQL + pgvector on Neon]
  brain.thoughts table
  - vector similarity search via <=> operator
  - JSONB for topics, people, action_items
  - user-scoped (USER_ID from $USER)
```

## Cost

| Component | Cost |
|-----------|------|
| Neon PostgreSQL (free tier) | $0.00/month |
| Embeddings (local CPU) | $0.00/month |
| Claude API metadata extraction | ~$0.01-0.02/month |
| **Total** | **~$0.01-0.02/month** |

## Troubleshooting

### "type vector does not exist"
You're connecting through the Neon pooler endpoint. Use the direct endpoint instead (remove `-pooler` from the hostname).

### "Could not resolve authentication method" for Claude API
Set the `ANTHROPIC_API_KEY` environment variable. Metadata extraction falls back to defaults without it, but you get better thought classification with it.

### Slow first capture/search
The `all-mpnet-base-v2` model (~420MB) downloads on first use. Subsequent runs are instant.

### Neon compute suspends
Neon auto-suspends after 5 minutes of inactivity. First query after suspension takes ~500ms to wake. This is normal and transparent.
