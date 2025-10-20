# Chris's Claude Code Toolkit

A comprehensive collection of custom agents and workflow commands for Claude Code.

## What's Included

### 🤖 Agents (9)

Specialized agents for various development tasks:

- **code-quality-reviewer** - Comprehensive code review focusing on quality, security, and performance
- **devops-deployment-specialist** - Deployment pipelines, infrastructure, containerization, and CI/CD
- **docs-scraper** - Fetch and save documentation from URLs as markdown
- **implementation-developer** - Write complete, production-ready code implementations
- **prompt-engineer-optimizer** - Transform technical specs into optimized prompts
- **solution-architect-planner** - Design technical solutions and create implementation plans
- **technical-writer** - Create comprehensive technical documentation
- **test-engineer-qa** - Create comprehensive test suites and validate code quality
- **ui-ux-frontend-engineer** - Design, implement, and improve user interfaces

### ⚡ Commands (19)

Workflow automation commands:

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
/plugin install file:///c:/users/chris/Documents/claude-toolkit-plugin
```

## Usage

After installation, all agents and commands will be available in your Claude Code sessions.

### Using Agents

Agents are invoked automatically based on your tasks, or you can explicitly request them:

```
"Use the code-quality-reviewer agent to review my recent changes"
"Let the solution-architect-planner design this feature"
```

### Using Commands

Commands are invoked with a forward slash:

```
/commit
/create-pr
/background "run comprehensive tests"
```

## Requirements

- Claude Code CLI
- Git (for git-related commands)

## Contributing

This is a personal toolkit, but feel free to fork and adapt for your own use!

## License

MIT License - Use freely in your own projects

## Version History

- **1.0.0** - Initial release with 9 agents and 19 commands
