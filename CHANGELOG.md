# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-07-12

Headline: the anti-persuasion-bombing **Veracity Layer** - structural enforcement for the
Truth-Over-Engagement contract, shipped end-to-end in the Open Brain (and mirrored to the pi
plugin via the bridge). A single model cannot reliably check itself; this release makes that
check structural rather than aspirational.

### Added - Veracity Layer

- **Truth-Over-Engagement contract** - the 7-clause behavioral floor (show work not persuade,
  restate plainly, no rhetorical escalation, no effusive apology, no unprompted avalanche,
  confidence-forward, re-examine-don't-cave-or-double-down).
- **Persuasion-bombing detector** - pure-code L0 scorer with a clause-to-signal bijection, plus a
  warn-mode Stop hook that scores each turn.
- **Evidence-independence guard** on NAL revision - repetition can no longer manufacture
  confidence; same-session, direct parent/child, or shared transitive-derivation-ancestry premises
  fuse to the more-confident premise unchanged.
- **V1 capture discount** - text or a turn carrying persuasion-bombing tells enters memory at a
  discounted `stv.c`, with a C_MAX ceiling a confident self-stamp cannot buy out; only ever lowers.
- **Turn-condition threading** - the Stop hook records a flagged turn's condition (30-min TTL) so
  same-session captures are discounted even when their text reads clean.
- **Recall veracity label** - `[LOW-VERACITY: produced under pushback]` on flagged atoms in search.
- **VL-6 veracity ranking** - low-veracity atoms auto-demote in search order (marginal penalty,
  cannot suppress a highly-relevant flawed atom - the label is that atom's primary defense).

### Added - Skills

- `/refute` - independent adversarial refuter (local Ollama model by default, escalates to a fresh
  cloud subagent for high-stakes claims; enforces a required strongest-counter-case).
- `/reexamine` - guided clause-7 re-examination of a challenged claim (fixed CHANGED/HELD verdict,
  no-apology + anti-avalanche constraints, `/refute` escalation for high-stakes).
- `/persuasion-score` - score arbitrary text with the L0 detector (the rhetoric axis).

### Fixed

- `loop_runner` `compose_dispatch` read `bead.body` but beads emit the task detail under
  `description`, so every worker dispatch got a title-only prompt. Now reads `description` with a
  `body` fallback, plus a regression test.

### Docs

- New "Veracity Layer" section in `CLAUDE.md` documenting how an agent uses the machinery. Corrected
  two claims the shipped code had falsified (enforcement "is being built"; revision confidence
  "strictly higher than either premise" - true only for independent evidence). Mirrored to the pi
  plugin's `AGENTS.md`.

### Versioning note

The plugin manifest (`.claude-plugin/plugin.json`) is bumped 2.2.0 -> 3.0.0 - the veracity layer is
a headline platform milestone, hence the major bump. (`setup.py` versions the vendored `beads`
package, not this plugin, so it stays at its own version.)

---

## [2.2.0] - 2026-06-24

### Added - Mayor v2 / v2.1 / v2.2 (bounded-concurrent loop runner)

All three workstreams were merged 2026-06-24. `--max-workers 1` (the default) preserves the original sequential single-stream loop, backward-compatible with all existing tests and runbooks.

> Versioning note: "Mayor vX.Y" is the internal feature codename for the loop runner, distinct from this plugin's semver. The plugin manifest (`.claude-plugin/plugin.json`) is bumped 2.0.0 -> 2.2.0 to align the release version with that pervasive "v2.2" identity and the live deploy; there is no separate 2.1.0 plugin release. (`setup.py` versions the vendored `beads` package, not this plugin, so it stays at its own version.)

**VA0a — Max-plan auth (v2.0)**
- Dispatch subprocess env strips `ANTHROPIC_API_KEY` so `claude -p` workers authenticate via Max-plan OAuth, not the API key.

**VA0b — Worktree-preserving code integration (v2.0)**
- Workers commit on a named branch `mayor/<bead_id>` (not detached HEAD) so commits are reachable.
- Verify command (`V`) runs with `cwd = worktree` so it sees the worker's committed changes.
- On V exit 0: Mayor (single-writer main thread) takes the merge lock, merges `mayor/<bead_id>` into the working branch, closes the bead, then tears down the worktree.
- On V != 0: worktree torn down without merge; code discarded; bead stays open for retry.

**VA1 — Rate-limit backpressure (v2.0)**
- `RATE_LIMITED` is classified distinctly from `FAILED`. A rate-limited bead is returned to the ready set; the governor pause-stops for a clean resume. No bead is ever burned by a provider rate-limit.

**VB1 + VB2 — Refinery: batch-then-bisect (v2.2)**
- Refinery replaces the VA0b serial merge with a single merge-slot covering a batch of V-passed branches.
- `order_by_score` anti-starvation scoring ensures long-waiting or already-retried branches merge first.
- Bisect isolates the conflicting branch in O(log K) verify calls; innocent branches in the same batch still land.
- On textual or semantic conflict: bead is relabeled `conflict:re-implement`, returned to the ready set, and re-dispatched against the advanced HEAD. Bounded by `--refinery-attempts` (default 2); on cap exhaustion the bead is escalated (left open, never respawned infinitely).
- `--batch-max 1` (the default) reproduces the VA0b serial path exactly.

**VD1 + VD2 — Pi parity**
- TypeScript twin at `src/loop-runner.ts` and `src/mayor-reconciler.ts` in `optivai-pi-plugin`.
- Parity corpus (`scripts/hooks/tests/mayor_parity_corpus.json`) asserts byte-identical verdicts across Python and TypeScript implementations.

### Architecture

- **Single-writer invariant:** only the Mayor main thread writes bead status. Workers return `WorkerResult` structs and never call `beads_close` or `beads_update`.
- **Verify-at-source close gate:** a bead closes only on V exit 0, never on a worker self-report.
- **Isolation:** git-worktree isolation. There is NO OS-level sandbox -- that was explicitly cut as a product-context bleed (git-worktrees are the Gas-Town-faithful isolation).
- **Reconciler (P2):** mechanical detector emits stuck-candidate events (never acts); a guard-ladder (terminal-state / stale-hook / spawning-window / TOCTOU) suppresses false positives; a cheap-tier AI judge decides kill/respawn/wait; fails safe to "wait" on any judge error.
- **Tier routing:** `route_model()` is LIVE -- it inspects bead labels and title keywords and passes `--model opus/sonnet/haiku` to each worker dispatch. This is functional behavior, not ledger-only decoration.
- **No conversational "Mayor" persona.** The Mayor is a CLI runner invoked as `cd <repo> && python3 scripts/loop_runner.py --molecule epic:<name> --max-workers N`. It does not auto-fire on a schedule (the launchd installer stays `--dry-run` until `--live`).
- **No Foreman tier.** The Foreman (Gas Town mid-tier crew boss) was deliberately not ported -- scale mismatch and it would break the single-writer invariant.

### New CLI flags (added to `_build_arg_parser`)

`--max-workers`, `--stuck-threshold`, `--spawning-window`, `--max-respawns`, `--batch-max`, `--refinery-attempts`

### New env vars

`OPTIVAI_LOOP_MAX_WORKERS`, `OPTIVAI_LOOP_STUCK_THRESHOLD_S`, `OPTIVAI_LOOP_SPAWNING_WINDOW_S`, `OPTIVAI_LOOP_MAX_RESPAWNS`, `OPTIVAI_LOOP_BATCH_MAX`, `OPTIVAI_LOOP_REFINERY_ATTEMPTS_MAX`

### New data structures (loop_runner.py)

`WorkerHandle`, `WorkerResult`, `MayorSummary`, `MergeCandidate`, `RefineOutcome` — plus `reconciler.py` (`StuckCandidate`, `ReconcileAction`) as a standalone module.

---

## [2.0.0] - 2026-04-18

### Changed — OptivAI Rebrand & Consolidation
- **Complete GrowthZone IP removal** — stripped all GZ credentials, infrastructure refs, branding
- **Beads prefix** — `gz`/`gzg` → `optivai`/`optivai-g`
- **Plugin manifest** — renamed to `optivai-claude-plugin` v2.0.0
- **Settings.json** — removed GZ MCP server, fixed till_done.py hook path
- **Auto-logger config** — sanitized, uses DATABASE_URL env var (Neon PostgreSQL)

### Added
- **37 commands** (up from 24) — added brain-*, bead-*, tana, db-connect, connect-jira, and more
- **Deterministic brain instructions** — concrete auto-search/capture triggers in CLAUDE.md
- **Beads source-of-truth doctrine** — "a task exists only when it has a bead" instructions
- **Keychain-based secrets** — ZAI_API_KEY, DATABASE_URL, ANTHROPIC_API_KEY all via macOS Keychain

### Security
- **IP scrub** — removed WRA18224, CHRIS_HUGHES, micronetonline, PartitionKey=36495, DW_DEV_STREAM, christopherhughesgz from all files
- **No plaintext secrets** in any committed files

---

## [1.2.0] - 2025-12-09

### Added

#### Automatic Activity Logging System
- **Hook-based logging infrastructure** - Comprehensive automatic activity tracking for Claude Code
- **PreToolUse hook** (`pre-tool-use.py`) - Captures all tool operations (Read, Write, Edit, Bash, Task, Grep, Glob, WebFetch, WebSearch, TodoWrite, SlashCommand)
- **UserPromptSubmit hook** (`user-prompt-submit.py`) - Captures user prompts for operation context
- **Core logging engine** (`log-writer.py`) - JSON Lines format with session tracking and daily rotation
- **Configuration system** (`auto-logger-config.json`) - Flexible control over logging behavior
- **Comprehensive documentation** (`.claude/hooks/README.md`) - 500+ line user guide

#### Logging Management Commands (6 new commands)
- `/install-logging-hooks` - One-command installation of logging system in any project
- `/enable-logging` - Enable automatic activity logging
- `/disable-logging` - Disable logging while preserving all existing logs
- `/view-logs [N]` - View recent activity logs with filtering options (default 20 entries)
- `/logging-config [--set KEY VALUE]` - View and update logging configuration
- `/export-logs [format]` - Export logs in JSON, CSV, or Markdown formats

#### Features
- **Automatic capture** - All Claude Code operations logged transparently (< 5ms overhead)
- **Session tracking** - Unique session IDs link all operations in a session
- **Daily log rotation** - Organized by date in `.claude/logs/agent-activity-YYYY-MM-DD.log`
- **Smart filtering** - Configurable ignore patterns for sensitive files
- **Multiple export formats** - JSON (analysis), CSV (spreadsheet), Markdown (reports)
- **Privacy-first** - All data stays local, no external network requests
- **Zero setup** - Install once per project, runs automatically

#### Documentation
- `HOOK-LOGGING-IMPLEMENTATION.md` - Complete implementation summary
- Updated `README.md` - Added Automatic Activity Logging section with quick start
- `.claude/hooks/README.md` - Comprehensive technical documentation and user guide

### Changed

- **Plugin version** - Updated from 1.1.0 to 1.2.0
- **Plugin manifest** - Auto-updated to include 6 new logging commands (23 → 29 total)
- **README.md** - Added comprehensive logging documentation section

### Technical Details

- **New files**: 12 (6 hook scripts/configs + 6 command files + documentation)
- **New lines of code**: ~2,500
- **Commands**: 23 → 29 (+26% increase)
- **Agents**: 40 (unchanged)

### Architecture

The logging system uses Claude Code's native hook system:
- Hooks configured in `.claude/settings.local.json`
- Hook scripts in `.claude/hooks/` (project-specific)
- Logs written to `.claude/logs/` (daily rotation)
- JSON Lines format for easy parsing and analysis

### Use Cases

- **Debugging**: Trace exact operations when troubleshooting
- **Auditing**: Maintain records of all Claude Code activities
- **Learning**: Understand usage patterns and workflows
- **Analytics**: Analyze tool usage, session duration, and patterns
- **Documentation**: Export session logs as documentation

## [1.1.0] - 2025-10-24

### Added

#### Agents (31 new agents)
- **Data Quality & Engineering (10 agents)**
  - `business-analyst` - Business process analysis and requirements gathering
  - `data-architect` - Data architecture design and enterprise data strategy
  - `data-engineer` - Data pipeline development and ETL processes
  - `data-governance-lead` - Data governance frameworks and policy implementation
  - `data-quality-analyst` - Data quality assessment, profiling, and validation
  - `data-quality-manager` - Data quality program management and strategy
  - `data-scientist` - Statistical analysis, ML modeling, and data insights
  - `data-steward` - Data stewardship and metadata management
  - `database-administrator` - Database optimization and administration
  - `subject-matter-expert` - Domain-specific expertise across industries

- **Business & Compliance (7 agents)**
  - `change-management-specialist` - Organizational change management
  - `compliance-officer` - Regulatory compliance and risk assessment
  - `financial-analyst` - Financial analysis and business case development
  - `immigration-law-sme` - Immigration law and visa compliance expertise
  - `product-manager` - Product strategy and roadmap planning
  - `product-owner` - Agile product ownership and backlog management
  - `program-manager` - Program management and portfolio coordination

- **Technical & Strategic (6 agents)**
  - `machine-learning-engineer` - ML/AI implementation and model deployment
  - `market-analysis-mgr` - Market research and competitive analysis
  - `senior-engineer` - Senior technical leadership and architecture
  - `solution-architect` - Enterprise solution architecture and system design
  - `strategic-planning-manager` - Strategic planning and business transformation
  - `user-research-expert` - User research, UX research, and customer insights

- **UX/UI Design (1 agent)**
  - `ux-ui-design-manager` - UX/UI design management and design systems

- **GenSI Orchestration (8 agents)**
  - `gensi-phase0-executor` - Foundation research: org profile, market research, current state
  - `gensi-phase1-initiative-planner` - Initiative planning and summary creation
  - `gensi-phase1-initiative-executor` - Strategic initiative document development
  - `gensi-phase2-initiative-worker` - Business model development per initiative
  - `gensi-phase3-initiative-worker` - Solution design with dependency management (AAP)
  - `gensi-phase4-initiative-worker` - Strategic planning with autonomous activation (AAP)
  - `gensi-phase5-initiative-worker` - Program planning and resource allocation (AAP)
  - `gensi-phase6-initiative-worker` - Execution planning and implementation roadmaps (AAP)

#### Commands (4 new commands)
- `/ai-role` - Activate specialized AI expert roles from registry
- `/gensi` - GenSI strategic planning with Autonomous Agentic Pipeline (AAP) orchestration
- `/gensi-nbc-full` - Legacy GenSI full execution workflow
- `/sync-fblai` - Sync business artifacts and standards from FBLAI private repository

#### Scripts & Tools
- `scripts/convert-fblai-roles.js` - Automated FBLAI to OptivAI agent conversion
- `scripts/update-manifest.js` - Automated manifest generation and version management
- `scripts/sync-fblai.js` - GitHub API integration for FBLAI content synchronization
- `scripts/test-agents.js` - Agent YAML frontmatter validation

#### Documentation
- `AGENT-CATALOG.md` - Comprehensive 40-agent reference guide organized by domain
- `INTEGRATION-COMPLETE.md` - Full FBLAI integration documentation and validation
- Enhanced `README.md` with complete agent listings, usage examples, and FBLAI features

### Changed

- **Plugin Manifest** - Updated to v1.1.0 with 40 agents and 23 commands
- **Keywords** - Added `data-quality`, `business-analysis`, `compliance`, `strategic-planning`, `gensi`, `fblai`, `enterprise`, `architecture`, `documentation`
- **README.md** - Complete rewrite with categorized agent listings, GenSI explanation, and comprehensive usage guide
- **Agent Distribution** - Now includes both Opus (19 agents) and Sonnet (22 agents) for balanced performance

### Fixed

- Removed duplicate YAML frontmatter from 8 GenSI orchestration agents
- Corrected model assignments (Opus for complex analysis, Sonnet for balanced tasks)
- Fixed agent descriptions to match capabilities accurately

## [1.0.0] - 2025-10-22

### Added

#### Initial Release
- **9 Development & Engineering Agents**
  - `code-quality-reviewer` - Comprehensive code review
  - `devops-deployment-specialist` - Deployment and infrastructure
  - `docs-scraper` - Documentation fetching and conversion
  - `implementation-developer` - Production-ready code implementation
  - `prompt-engineer-optimizer` - Prompt optimization for LLMs
  - `solution-architect-planner` - Technical solution design and planning
  - `technical-writer` - Technical documentation creation
  - `test-engineer-qa` - Test suite creation and quality validation
  - `ui-ux-frontend-engineer` - UI/UX implementation and design

- **19 Workflow Commands**
  - `/act` - GitHub Actions workflow runner
  - `/add-to-changelog` - Changelog management
  - `/background` - Background Claude instance execution
  - `/commit` - Enhanced git commit workflow
  - `/config-logs` - Configuration logging
  - `/context-check` - Context verification
  - `/create-jtbd` - Jobs-To-Be-Done document creation
  - `/create-pr` - Pull request creation
  - `/create-prd` - Product Requirements Document generation
  - `/create-prp` - Project Requirements & Planning document creation
  - `/export-context` - Context export functionality
  - `/history` - Command history viewer
  - `/load-context` - Context loading
  - `/new-session` - New session with clean context
  - `/prime-agent` - Agent context priming
  - `/quick-context` - Quick context snapshot
  - `/search-context` - Context search
  - `/summary` - Conversation summary generation
  - `/Testing Configuration` - Testing reference documentation

#### Foundation
- Initial plugin structure with agents and commands directories
- Plugin manifest (plugin.json) with metadata and component definitions
- README with installation instructions and basic usage
- MIT License
- .gitignore for common development files

---

## Release Statistics

### Version 1.1.0
- **Agents:** 9 → 40 (+344% increase)
- **Commands:** 19 → 23 (+21% increase)
- **Documentation:** 2 → 4 files (+100% increase)
- **Scripts:** 0 → 4 automation scripts
- **Lines of Code:** +8,733 lines added

### Version 1.0.0
- Initial release with 9 agents and 19 commands
- Foundation for enterprise AI toolkit

---

## Integration History

**v1.1.0** represents the full integration of FBLAI (FeedbackLoop.AI) Adaptive Development Environment with the OptivAI plugin, creating a comprehensive enterprise-grade AI toolkit covering:
- Software development workflows
- Data quality and governance
- Business analysis and compliance
- Strategic planning (GenSI framework)
- Technical architecture and leadership

---

## Links

- [Repository](https://github.com/feedbackloopai-llc/optivai-claude-plugin)
- [Agent Catalog](AGENT-CATALOG.md)
- [Integration Documentation](INTEGRATION-COMPLETE.md)
- [Release Notes](RELEASE-NOTES.md)

---

**Maintained by:** [FeedbackLoop.AI LLC](https://github.com/feedbackloopai-llc)
