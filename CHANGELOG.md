# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
