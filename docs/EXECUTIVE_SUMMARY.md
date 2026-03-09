# Claude Code Activity Logging - Executive Summary

## What Is This?

The **OptivAI Claude Plugin** is an observability solution for AI-assisted software development. It automatically tracks all interactions between developers and Claude Code (Anthropic's AI coding assistant), storing this data in PostgreSQL for analysis, audit, and operational intelligence.

## Business Value

### 1. Visibility & Governance

**Problem:** When AI assistants modify code, there's no automatic record of what was done, why, or by whom.

**Solution:** Every AI operation is logged with full context:
- What files were read/written
- What commands were executed
- What prompts the developer gave
- When and in which project

### 2. Productivity Analytics

**Capability:** Understand how AI tools are being used across the organization:
- Which projects use AI assistance most
- What types of tasks AI handles
- Time spent on AI-assisted vs manual work
- Adoption trends over time

### 3. Audit & Compliance

**Capability:** Maintain complete audit trails for regulated environments:
- Immutable event records in PostgreSQL
- Natural key hashing for data integrity
- Correlation of changes to specific sessions

### 4. AI-Enabled Continuity

**Capability:** AI assistants can "remember" previous work:
- When returning to a project, the AI can review its own history
- Reduces context-switching overhead
- Enables smarter continuation of previous tasks

### 5. Session Continuity (Propulsion Principle)

**Capability:** Seamless handoff between work sessions:
- **Agent Identity Tracking**: Each agent is identified by `{project}/{role}/{user}` pattern
- **Handoff System**: `/handoff` command captures work state before ending sessions
- **Propulsion Check**: `/prime-agent` detects pending work and prompts immediate continuation
- **Core Principle**: "If there's work on your hook, RUN IT" - agents immediately execute pending tasks

## How It Works

```
Developer uses Claude Code
         │
         ▼
    [Automatic Hooks]
    Capture every operation
         │
         ▼
    [Local Log Files]
    JSON, per-project
         │
         ▼
    [Sync Daemon]
    Every 60 seconds
         │
         ▼
    [PostgreSQL]
    RAW_EVENTS table
         │
         ▼
    [Analytics & Reporting]
    Dashboards, queries, alerts
```

## Data Captured

| Data Type | Examples |
|-----------|----------|
| **User Prompts** | "Fix the bug in authentication" |
| **File Operations** | Read `src/auth.py`, Write `tests/test_auth.py` |
| **Commands** | `git commit`, `npm test`, `docker build` |
| **Searches** | Grep for "TODO", Glob for `*.py` |
| **Context** | Project name, timestamp, session ID |
| **Agent Identity** | `my-project/crew/chris-hughes` (BD_ACTOR pattern) |
| **Session Tokens** | Input, output, cache write, cache read token counts |
| **API-Equiv Cost** | Per-session cost estimate by model pricing |

## Key Metrics Available

- **Event Volume**: Total AI operations per day/week/month
- **Project Coverage**: Which codebases use AI assistance
- **Operation Mix**: Read vs Write vs Execute distribution
- **Token Usage**: Input, output, and cache token counts per session
- **API-Equivalent Cost**: Per-session and monthly cost estimates by model
- **Session Duration**: How long AI-assisted sessions last
- **User Activity**: Per-developer AI usage patterns
- **Agent Analytics**: Per-agent activity tracking (BD_ACTOR pattern)
- **Session Continuity**: Handoff/resume patterns and pending work tracking

## Technical Requirements

| Component | Requirement |
|-----------|-------------|
| **Claude Code** | Installed and configured |
| **Python** | 3.8 or higher |
| **PostgreSQL** | Neon PostgreSQL account |
| **Storage** | ~1MB/day typical log volume |

## Security & Privacy

- **Credentials**: Stored locally, never in logs
- **Prompt Content**: Configurable truncation
- **File Contents**: NOT logged (only paths)
- **Access Control**: Standard PostgreSQL RBAC

## Implementation Timeline

| Phase | Duration | Activities |
|-------|----------|------------|
| **Setup** | 1 hour | Install plugin, configure credentials |
| **Testing** | 1 day | Verify logging, test queries |
| **Rollout** | 1 week | Deploy to team, document processes |
| **Analytics** | Ongoing | Build dashboards, establish baselines |

## Cost Considerations

| Item | Impact |
|------|--------|
| **PostgreSQL Compute** | Minimal - sync runs 1x/minute with small batches |
| **PostgreSQL Storage** | ~30MB/month per active developer |
| **Developer Time** | 1 hour initial setup per developer |
| **Maintenance** | Near-zero - runs automatically |

## Success Criteria

1. **100% Coverage**: All Claude Code operations logged
2. **<1 min Latency**: Events appear in PostgreSQL within 60 seconds
3. **Zero Impact**: No performance degradation to Claude Code
4. **Queryable History**: Analysts can query AI activity patterns

## Next Steps

1. **Install** the plugin (see README.md)
2. **Configure** PostgreSQL credentials
3. **Verify** logging is working
4. **Build** initial dashboards
5. **Establish** usage baselines

## Contact

For questions or support, contact the Data Engineering team or open an issue in the GitHub repository.
