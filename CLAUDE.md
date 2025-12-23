# Global Development Standards

## Spec-Driven Development with GitHub Spec Kit

For **new software development projects** (not simple scripts or quick fixes), use **GitHub Spec Kit** for specification-driven development.

**Repository:** https://github.com/github/spec-kit

### When to Use Spec Kit

| Use Spec Kit | Skip Spec Kit |
|--------------|---------------|
| Greenfield projects (new applications) | Simple scripts or utilities |
| Complex multi-component features | Quick bug fixes |
| Brownfield modernization/refactoring | Small isolated changes |
| Mission-critical software | Rapid prototyping |
| When exploring multiple tech stacks | Trivial features |

### Spec Kit Workflow

1. **Constitution** (`/speckit.constitution`) - Establish project principles, code standards, testing requirements
2. **Specify** (`/speckit.specify`) - Define requirements and user stories (focus on WHAT, not HOW)
3. **Clarify** (`/speckit.clarify`) - Resolve ambiguities before planning
4. **Plan** (`/speckit.plan`) - Technical approach, architecture, data models
5. **Validate** - Manual review of the plan
6. **Tasks** (`/speckit.tasks`) - Generate implementation task breakdown
7. **Implement** (`/speckit.implement`) - Execute tasks with TDD

### Key Principles

- **Intent-driven**: Define the "what" and "why" before the "how"
- **No one-shot generation**: Use multi-step refinement
- **Technology-agnostic specs**: Requirements shouldn't prescribe implementation
- **Constitution persists**: Foundational guidelines apply across all features

### File Structure

```
project-root/
├── .specify/
│   ├── memory/
│   │   └── constitution.md      # Project principles (persistent)
│   ├── specs/
│   │   └── 001-feature-name/
│   │       ├── spec.md          # Requirements
│   │       ├── plan.md          # Technical approach
│   │       ├── tasks.md         # Implementation tasks
│   │       └── contracts/       # API specs
│   └── templates/
└── [implementation files]
```

### Installation

```bash
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git
specify init <project-name> --ai claude
```

---

## General Development Standards

- Simple/clean/maintainable over clever/complex
- Match existing code style within files
- Ask permission before major architectural changes
- Prefer editing existing files over creating new ones
- Never use `--no-verify` when committing

## Activity Logging & Memory System

This machine has Claude Code activity logging and a persistent memory system enabled.

### Activity Logs
All tool operations and prompts are logged locally to `.claude/logs/` for session continuity. Logs include subagent lineage tracking (which agent performed each action).

### Memory System
Persistent memory files at `~/.claude/claude-code-memory/`:

| File | Purpose |
|------|---------|
| `session_state.yaml` | Current session context and focus |
| `planned_tasks.yaml` | Tasks to complete (remove when done) |
| `work_log.yaml` | Chronological action history |
| `recovery_checkpoint.yaml` | Crash recovery context |

### Commands
- `/prime-agent` - Load activity logs + memory system context
- `/quick-context` - Fast context loading
- `/new-session` - Start fresh session with new ID
