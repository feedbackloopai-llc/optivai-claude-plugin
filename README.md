# Chris's Claude Code Toolkit

A comprehensive enterprise-grade AI agent toolkit integrating OptivAI development workflow agents with FBLAI business and data quality expertise.

## What's Included

### 🤖 Agents (40)

#### Development & Engineering Agents (9)
- **code-quality-reviewer** - Comprehensive code review focusing on quality, security, and performance
- **devops-deployment-specialist** - Deployment pipelines, infrastructure, containerization, and CI/CD
- **docs-scraper** - Fetch and save documentation from URLs as markdown
- **implementation-developer** - Write complete, production-ready code implementations
- **prompt-engineer-optimizer** - Transform technical specs into optimized prompts
- **solution-architect-planner** - Design technical solutions and create implementation plans
- **technical-writer** - Create comprehensive technical documentation
- **test-engineer-qa** - Create comprehensive test suites and validate code quality
- **ui-ux-frontend-engineer** - Design, implement, and improve user interfaces

#### Data Quality & Engineering Agents (10)
- **business-analyst** - Business process analysis and requirements gathering
- **data-architect** - Data architecture design and enterprise data strategy
- **data-engineer** - Data pipeline development and ETL processes
- **data-governance-lead** - Data governance frameworks and policy implementation
- **data-quality-analyst** - Data quality assessment, profiling, and validation
- **data-quality-manager** - Data quality program management and strategy
- **data-scientist** - Statistical analysis, ML modeling, and data insights
- **data-steward** - Data stewardship and metadata management
- **database-administrator** - Database optimization and administration
- **subject-matter-expert** - Domain-specific expertise across industries

#### Business & Compliance Agents (7)
- **change-management-specialist** - Organizational change management
- **compliance-officer** - Regulatory compliance and risk assessment
- **financial-analyst** - Financial analysis and business case development
- **immigration-law-sme** - Immigration law and visa compliance expertise
- **product-manager** - Product strategy and roadmap planning
- **product-owner** - Agile product ownership and backlog management
- **program-manager** - Program management and portfolio coordination

#### Technical & Strategic Roles (6)
- **machine-learning-engineer** - ML/AI implementation and model deployment
- **market-analysis-mgr** - Market research and competitive analysis
- **senior-engineer** - Senior technical leadership and architecture
- **solution-architect** - Enterprise solution architecture and system design
- **strategic-planning-manager** - Strategic planning and business transformation
- **user-research-expert** - User research, UX research, and customer insights

#### UX/UI Design (1)
- **ux-ui-design-manager** - UX/UI design management and design systems

#### GenSI Strategic Planning Agents (8)
- **gensi-phase0-executor** - Foundation research: org profile, market research, current state
- **gensi-phase1-initiative-planner** - Initiative planning and summary creation
- **gensi-phase1-initiative-executor** - Strategic initiative document development
- **gensi-phase2-initiative-worker** - Business model development per initiative
- **gensi-phase3-initiative-worker** - Solution design with dependency management
- **gensi-phase4-initiative-worker** - Strategic planning with autonomous activation
- **gensi-phase5-initiative-worker** - Program planning and resource allocation
- **gensi-phase6-initiative-worker** - Execution planning and implementation roadmaps

### ⚡ Commands (29)

#### Development Workflow Commands (19)
- **act** - GitHub Actions workflow runner
- **add-to-changelog** - Add entries to CHANGELOG
- **background** - Run Claude Code instance in background
- **commit** - Enhanced git commit workflow
- **config-logs** - View configuration logs
- **context-check** - Check current context
- **create-jtbd** - Create Jobs-To-Be-Done documents
- **create-pr** - Create pull requests with templates
- **create-prd** - Create Product Requirements Documents
- **create-prp** - Create Project Requirements & Planning documents
- **export-context** - Export conversation context
- **history** - View command history
- **load-context** - Load saved context
- **new-session** - Start a new session with clean context
- **prime-agent** - Prime an agent with context
- **quick-context** - Quick context snapshot
- **search-context** - Search through saved contexts
- **summary** - Generate conversation summary
- **Testing Configuration** - Reference for testing configuration

#### Automatic Activity Logging Commands (6)
- **install-logging-hooks** - Install automatic activity logging hooks in current project
- **enable-logging** - Enable automatic Claude Code activity logging
- **disable-logging** - Disable logging (preserves all logs)
- **view-logs** - View recent activity logs with filtering
- **logging-config** - Configure logging settings
- **export-logs** - Export logs to JSON, CSV, or Markdown formats

#### FBLAI Business & Strategy Commands (4)
- **ai-role** - Activate specialized AI expert roles from registry
- **gensi** - GenSI strategic planning with Autonomous Agentic Pipeline (AAP)
- **gensi-nbc-full** - Legacy GenSI full execution workflow
- **sync-fblai** - Sync business artifacts and standards from FBLAI repository

## 🔍 Automatic Activity Logging

**New in v1.2.0**: Hook-based automatic activity logging system for comprehensive activity tracking.

### What It Does

The automatic activity logging system uses Claude Code hooks to capture:

- ✅ **All tool operations**: Read, Write, Edit, Bash, Task, Grep, Glob, WebFetch, etc.
- ✅ **User prompts**: Your inputs for context
- ✅ **Session tracking**: Links all operations together
- ✅ **Daily rotation**: Organized by date in JSON Lines format
- ✅ **Zero overhead**: < 5ms per operation

### Quick Start

```bash
# Install hooks in your project
/install-logging-hooks

# Enable logging
/enable-logging

# Work normally - everything is automatically logged
# ... use Claude Code ...

# View what happened
/view-logs

# Export for analysis
/export-logs markdown
```

### Features

**Automatic Capture**
- No manual logging calls needed
- Hooks capture operations transparently
- Context preservation across operations
- Session-based organization

**Flexible Export**
- JSON: Machine-readable, programmatic analysis
- CSV: Spreadsheet analysis, data science
- Markdown: Human-readable reports with statistics

**Smart Configuration**
- Enable/disable specific log types
- Ignore patterns for sensitive files
- Adjust verbosity and truncation
- Session tracking on/off

**Zero Setup After Installation**
- Install once per project
- Runs automatically
- Minimal performance impact
- All data stays local

### Example Log Entry

```json
{
  "timestamp": "2025-12-09T20:15:30.123Z",
  "operation": "read",
  "prompt": "read: src/app.ts",
  "session_id": "session-20251209-201530-a1b2c3d4",
  "time": "20:15:30"
}
```

### Use Cases

- **Debugging**: Trace exactly what happened when something went wrong
- **Auditing**: Maintain records of all Claude Code activities
- **Learning**: Understand patterns in how you use Claude Code
- **Analytics**: Analyze tool usage, session duration, patterns
- **Documentation**: Export session logs as documentation

### Architecture

The logging system consists of:

- **Hooks**: `PreToolUse` and `UserPromptSubmit` hooks in `.claude/settings.local.json`
- **Scripts**: Python hook handlers in `.claude/hooks/`
- **Logger**: Core logging engine (`log-writer.py`)
- **Config**: Behavior configuration (`auto-logger-config.json`)
- **Logs**: Daily log files in `.claude/logs/`

For complete documentation, see [.claude/hooks/README.md](.claude/hooks/README.md)

## FBLAI Integration

This plugin integrates the **FBLAI (FeedbackLoop.AI) Adaptive Development Environment**, providing:

- **24 specialized business agents** covering data quality, compliance, strategy, and domain expertise
- **GenSI strategic planning framework** with multi-phase orchestration (Phases 0-6)
- **Autonomous Agentic Pipeline (AAP)** for dynamic initiative scaling
- **Business artifact generation** including SOWs, strategic plans, and market research
- **AI role registry system** for specialized expert activation

### What is GenSI?

GenSI (Generative Strategic Intelligence) is a multi-phase strategic planning framework that uses autonomous agents to:

1. **Phase 0**: Foundation research (org profile, market research, current state)
2. **Phase 1**: Initiative planning and strategic initiative creation
3. **Phase 2**: Business model development per initiative
4. **Phase 3**: Solution design with autonomous dependency management
5. **Phase 4**: Strategic planning with AAP activation
6. **Phase 5**: Program planning and resource allocation
7. **Phase 6**: Execution planning and implementation roadmaps

## Installation

### Option 1: Via Marketplace (Recommended)

If this plugin is in a marketplace you've added:

```bash
/plugin install chris-claude-toolkit
```

### Option 2: Direct from GitHub

```bash
/plugin install github:feedbackloopai-llc/optivai-claude-plugin
```

### Option 3: Local Development

```bash
/plugin install file:///c:/users/chris/Documents/optivai-claude-plugin
```

## Usage

After installation, all agents and commands will be available in your Claude Code sessions.

### Using Agents

Agents are invoked automatically based on your tasks, or you can explicitly request them:

```
"Use the code-quality-reviewer agent to review my recent changes"
"Let the solution-architect-planner design this feature"
"I need the data-quality-analyst to assess our customer data"
```

### Using Commands

Commands are invoked with a forward slash:

```bash
# Development workflow
/commit
/create-pr
/background "run comprehensive tests"

# Business & strategic planning
/ai-role data-quality-analyst
/gensi
/sync-fblai
```

### Example: Using AI Roles

Activate specialized expert roles for domain-specific tasks:

```bash
# Activate data quality analyst
/ai-role data-quality-analyst

# Activate compliance officer
/ai-role compliance-officer

# Activate strategic planning manager
/ai-role strategic-planning-manager
```

### Example: GenSI Strategic Planning

Run comprehensive strategic planning:

```bash
# Initialize GenSI with strategic planning request
/gensi

# Follow prompts to define:
# - Organization context
# - Strategic objectives
# - Number of initiatives (1-10)
# - Market positioning

# GenSI will execute Phases 0-6 automatically
```

## Requirements

- Claude Code CLI
- Git (for git-related commands)
- GitHub Personal Access Token (for `/sync-fblai` command)

## Agent Catalog

For a comprehensive catalog of all 40 agents organized by domain, see [AGENT-CATALOG.md](AGENT-CATALOG.md).

For detailed integration documentation, see [INTEGRATION-COMPLETE.md](INTEGRATION-COMPLETE.md).

## Contributing

This is a personal toolkit, but feel free to fork and adapt for your own use!

## License

MIT License - Use freely in your own projects

## Version History

- **1.1.0** - FBLAI integration: Added 31 agents, 4 commands, GenSI framework (October 2025)
- **1.0.0** - Initial release with 9 agents and 19 commands

## Maintainer

**FeedbackLoop.AI LLC**
- GitHub: [@feedbackloopai-llc](https://github.com/feedbackloopai-llc)
- Repository: [optivai-claude-plugin](https://github.com/feedbackloopai-llc/optivai-claude-plugin)
