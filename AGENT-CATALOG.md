# Agent Catalog

Comprehensive reference guide for all 40 specialized agents in the OptivAI + FBLAI integrated plugin.

## Quick Reference

| Domain | Agent Count | Models Used |
|--------|-------------|-------------|
| Development & Engineering | 9 | Opus (5), Sonnet (4) |
| Data Quality & Engineering | 10 | Opus (6), Sonnet (4) |
| Business & Compliance | 7 | Opus (3), Sonnet (4) |
| Technical & Strategic | 7 | Opus (5), Sonnet (2) |
| GenSI Orchestration | 8 | Sonnet (8) |
| **Total** | **40** | **Opus (19), Sonnet (22)** |

---

## Development & Engineering Agents

### code-quality-reviewer
**Model:** Opus | **Color:** Yellow

Comprehensive code review focusing on quality, security, performance, and best practices. Reviews recently written or modified code.

**Use when:**
- After writing new functions or features
- Before committing changes
- Refactoring existing code
- Need security vulnerability analysis

**Key capabilities:**
- Security vulnerability detection
- Performance optimization recommendations
- Code quality assessment
- Best practices enforcement

---

### devops-deployment-specialist
**Model:** Opus | **Color:** Green

Setup deployment pipelines, configure infrastructure, containerize applications, implement CI/CD workflows, and establish monitoring.

**Use when:**
- Need to deploy applications to cloud platforms
- Setting up Docker/Kubernetes configurations
- Creating CI/CD pipelines
- Infrastructure as code (Terraform/CloudFormation)

**Key capabilities:**
- Docker/Kubernetes setup
- CI/CD pipeline configuration
- Cloud deployment (AWS, Azure, GCP)
- Infrastructure automation

---

### docs-scraper
**Model:** Sonnet

Fetch and save documentation from URLs as properly formatted markdown files for offline reference or analysis.

**Use when:**
- Need to save API documentation offline
- Building knowledge base from web sources
- Creating documentation archives

**Key capabilities:**
- URL content fetching
- HTML to markdown conversion
- Batch processing multiple URLs

---

### implementation-developer
**Model:** Opus | **Color:** Red

Write complete, production-ready code implementations with no placeholders or TODOs. Translates specifications into fully functional code.

**Use when:**
- Need complete feature implementation
- Converting specs/designs to working code
- Building deployment-ready components

**Key capabilities:**
- Full feature implementation
- Production-ready code
- Complete error handling
- No placeholders or partial code

---

### prompt-engineer-optimizer
**Model:** Opus | **Color:** Red

Transform technical specifications into optimized prompts for downstream development agents.

**Use when:**
- Converting architecture docs to development prompts
- Refining requirements for LLM agents
- Resolving ambiguities in technical specifications

**Key capabilities:**
- Prompt optimization for specific models
- Ambiguity detection and resolution
- Technical specification refinement

---

### solution-architect-planner
**Model:** Opus | **Color:** Blue

Design technical solutions and create implementation plans from requirements. Focuses on tactical planning and task breakdown.

**Use when:**
- Designing new features or systems
- Breaking down complex projects
- Creating implementation roadmaps
- Need migration strategies

**Key capabilities:**
- Requirements to technical specs translation
- Task decomposition and sequencing
- Architectural trade-off evaluation
- Risk assessment

---

### technical-writer
**Model:** Sonnet | **Color:** Purple

Create comprehensive technical documentation including API docs, architecture documents, README files, and user guides.

**Use when:**
- Need API documentation
- Creating architecture documents
- Writing user guides
- Documenting complex systems

**Key capabilities:**
- API documentation
- Architecture documentation
- Technical writing best practices
- Clear explanations of complex concepts

---

### test-engineer-qa
**Model:** Opus

Create comprehensive test suites, validate code quality, identify edge cases, and ensure robust test coverage.

**Use when:**
- Writing unit/integration tests
- Need comprehensive test coverage
- Testing new features
- Validating code quality

**Key capabilities:**
- Test suite creation
- Edge case identification
- Coverage analysis
- Bug detection

---

### ui-ux-frontend-engineer
**Model:** Sonnet | **Color:** Cyan

Design, implement, and improve user interfaces with focus on implementation and coding.

**Use when:**
- Building UI components
- Implementing design mockups
- Frontend architecture
- Accessibility improvements

**Key capabilities:**
- UI component implementation
- Design system integration
- Accessibility compliance (WCAG)
- Frontend architecture

---

## Data Quality & Engineering Agents

### business-analyst
**Model:** Sonnet | **Color:** Cyan

Business process analysis, requirements gathering, and business case development.

**Use when:**
- Defining business requirements
- Process analysis and improvement
- Stakeholder requirement gathering

**Key capabilities:**
- Requirements elicitation
- Business process modeling
- Stakeholder management

---

### data-architect
**Model:** Opus | **Color:** Purple

Data architecture design, enterprise data strategy, and data modeling.

**Use when:**
- Designing data architecture
- Creating enterprise data strategies
- Data modeling and schema design

**Key capabilities:**
- Data architecture patterns
- Enterprise data strategy
- Data modeling best practices

---

### data-engineer
**Model:** Sonnet | **Color:** Green

Data pipeline development, ETL processes, and data integration.

**Use when:**
- Building data pipelines
- ETL development
- Data integration projects

**Key capabilities:**
- Pipeline architecture
- ETL design and implementation
- Data integration patterns

---

### data-governance-lead
**Model:** Opus | **Color:** Purple

Data governance frameworks, policy implementation, and compliance management.

**Use when:**
- Establishing data governance
- Creating data policies
- Compliance requirements

**Key capabilities:**
- Governance framework design
- Policy development
- Compliance management

---

### data-quality-analyst
**Model:** Opus | **Color:** Blue

Data quality assessment, profiling, validation, and anomaly detection.

**Use when:**
- Assessing data quality
- Creating validation rules
- Identifying data issues

**Key capabilities:**
- Data profiling
- Quality assessment
- Validation rule design
- Anomaly detection

---

### data-quality-manager
**Model:** Opus | **Color:** Blue

Data quality program management, strategy development, and quality metrics.

**Use when:**
- Managing data quality programs
- Defining quality strategy
- Establishing quality metrics

**Key capabilities:**
- Program management
- Quality strategy
- Metrics definition

---

### data-scientist
**Model:** Opus | **Color:** Purple

Statistical analysis, machine learning modeling, and data insights.

**Use when:**
- Statistical analysis
- ML model development
- Data exploration and insights

**Key capabilities:**
- Statistical analysis
- ML modeling
- Feature engineering
- Model evaluation

---

### data-steward
**Model:** Sonnet | **Color:** Cyan

Data stewardship, metadata management, and data cataloging.

**Use when:**
- Managing data assets
- Metadata management
- Data cataloging

**Key capabilities:**
- Data stewardship practices
- Metadata management
- Data catalog maintenance

---

### database-administrator
**Model:** Sonnet | **Color:** Green

Database optimization, administration, performance tuning, and backup/recovery.

**Use when:**
- Database performance issues
- Database administration
- Backup and recovery planning

**Key capabilities:**
- Performance tuning
- Query optimization
- Backup/recovery strategies
- Database security

---

### subject-matter-expert
**Model:** Sonnet | **Color:** Yellow

Domain-specific expertise across various industries and business domains.

**Use when:**
- Need industry-specific knowledge
- Domain expertise required
- Subject matter guidance

**Key capabilities:**
- Industry knowledge
- Best practices
- Domain-specific insights

---

## Business & Compliance Agents

### change-management-specialist
**Model:** Sonnet | **Color:** Yellow

Organizational change management, stakeholder engagement, and change adoption strategies.

**Use when:**
- Managing organizational change
- Planning change initiatives
- Stakeholder engagement

**Key capabilities:**
- Change management frameworks
- Stakeholder analysis
- Communication planning
- Adoption strategies

---

### compliance-officer
**Model:** Opus | **Color:** Red

Regulatory compliance, risk assessment, and compliance program management.

**Use when:**
- Compliance assessments
- Regulatory requirements
- Risk management

**Key capabilities:**
- Regulatory compliance (GDPR, HIPAA, SOX)
- Risk assessment
- Audit preparation
- Policy development

---

### financial-analyst
**Model:** Opus | **Color:** Blue

Financial analysis, business case development, ROI calculations, and financial modeling.

**Use when:**
- Financial analysis
- Business case development
- ROI calculations
- Budget planning

**Key capabilities:**
- Financial modeling
- Cost-benefit analysis
- ROI calculation
- Budget forecasting

---

### immigration-law-sme
**Model:** Opus | **Color:** Red

Immigration law expertise, visa compliance, and immigration process guidance.

**Use when:**
- Immigration compliance
- Visa processes
- Employment authorization

**Key capabilities:**
- Immigration law knowledge
- Visa process guidance
- Compliance requirements

---

### product-manager
**Model:** Sonnet | **Color:** Cyan

Product strategy, roadmap planning, feature prioritization, and product lifecycle management.

**Use when:**
- Product strategy development
- Roadmap planning
- Feature prioritization

**Key capabilities:**
- Product strategy
- Roadmap development
- Feature prioritization
- Stakeholder management

---

### product-owner
**Model:** Sonnet | **Color:** Cyan

Agile product ownership, backlog management, and sprint planning.

**Use when:**
- Agile product ownership
- Backlog management
- User story creation

**Key capabilities:**
- Backlog prioritization
- User story writing
- Sprint planning
- Stakeholder communication

---

### program-manager
**Model:** Sonnet | **Color:** Blue

Program management, portfolio coordination, and multi-project oversight.

**Use when:**
- Managing multiple projects
- Program coordination
- Portfolio management

**Key capabilities:**
- Program planning
- Resource allocation
- Risk management
- Stakeholder reporting

---

## Technical & Strategic Roles

### machine-learning-engineer
**Model:** Opus | **Color:** Purple

ML/AI implementation, model deployment, and MLOps.

**Use when:**
- ML model implementation
- Model deployment
- ML pipeline development

**Key capabilities:**
- ML model development
- Model deployment (MLOps)
- Pipeline automation
- Model monitoring

---

### market-analysis-mgr
**Model:** Opus | **Color:** Blue

Market research, competitive analysis, and market strategy.

**Use when:**
- Market research
- Competitive analysis
- Market sizing

**Key capabilities:**
- Market analysis
- Competitive intelligence
- Market segmentation
- Trend analysis

---

### senior-engineer
**Model:** Opus | **Color:** Purple

Senior technical leadership, architecture decisions, and mentorship.

**Use when:**
- Technical leadership needed
- Architecture decisions
- Code review and mentorship

**Key capabilities:**
- Technical leadership
- System architecture
- Code quality standards
- Team mentorship

---

### solution-architect
**Model:** Opus | **Color:** Purple

Enterprise solution architecture, cloud architecture, and technology strategy.

**Use when:**
- Enterprise architecture
- Cloud migration strategy
- Technology evaluation

**Key capabilities:**
- Enterprise architecture
- Cloud architecture (AWS, Azure, GCP)
- Technology assessment
- Architecture governance

---

### strategic-planning-manager
**Model:** Opus | **Color:** Purple

Strategic planning, business transformation, and organizational strategy.

**Use when:**
- Strategic planning
- Business transformation
- Long-term planning

**Key capabilities:**
- Strategic planning
- Business transformation
- Vision and mission development

---

### user-research-expert
**Model:** Sonnet | **Color:** Cyan

User research, UX research, customer insights, and usability testing.

**Use when:**
- User research
- Usability testing
- Customer insights

**Key capabilities:**
- User research methodologies
- Usability testing
- Customer journey mapping
- Research analysis

---

### ux-ui-design-manager
**Model:** Sonnet | **Color:** Cyan

UX/UI design management, design systems, and design leadership.

**Use when:**
- Design system development
- UX/UI design leadership
- Design team management

**Key capabilities:**
- Design system creation
- Design leadership
- UX/UI best practices
- Design team coordination

---

## GenSI Strategic Planning Agents

These 8 agents work together in the GenSI (Generative Strategic Intelligence) framework for multi-phase strategic planning.

### gensi-phase0-executor
**Model:** Sonnet

Phase 0 foundation research - creates organization profile, market research, and current state assessment.

**Outputs:**
- Organization profile
- Market research
- Current state assessment

---

### gensi-phase1-initiative-planner
**Model:** Sonnet

Phase 1 Step 1 - Creates initiative summary plan listing all strategic initiatives.

**Outputs:**
- Initiative summary plan

---

### gensi-phase1-initiative-executor
**Model:** Sonnet

Phase 1 Step 2 - Creates comprehensive strategic initiative documents (dynamically invoked N times).

**Outputs:**
- Strategic initiative documents (one per initiative)

---

### gensi-phase2-initiative-worker
**Model:** Sonnet

Phase 2 business model development for each initiative (dynamically invoked N times).

**Outputs:**
- Business model canvas per initiative
- Revenue model analysis

---

### gensi-phase3-initiative-worker
**Model:** Sonnet

Phase 3 solution design with autonomous dependency management (AAP pattern).

**Outputs:**
- Solution design documents
- Technical architecture

---

### gensi-phase4-initiative-worker
**Model:** Sonnet

Phase 4 strategic planning with autonomous activation (AAP pattern).

**Outputs:**
- Strategic plans per initiative
- Success metrics

---

### gensi-phase5-initiative-worker
**Model:** Sonnet

Phase 5 program planning and resource allocation (AAP pattern).

**Outputs:**
- Program plans
- Resource allocation strategies

---

### gensi-phase6-initiative-worker
**Model:** Sonnet

Phase 6 execution planning and implementation roadmaps (AAP pattern).

**Outputs:**
- Execution plans
- Implementation roadmaps
- Timeline and milestones

---

## Usage Guide

### How to Invoke Agents

**Option 1: Explicit Request**
```
"Use the data-quality-analyst agent to assess our customer data"
"Let the solution-architect-planner design this migration"
```

**Option 2: AI Role Command**
```bash
/ai-role data-quality-analyst
/ai-role compliance-officer
```

**Option 3: Automatic Selection**
Claude Code automatically selects appropriate agents based on task context.

### Model Selection Guide

**Opus Agents (Complex Analysis)**
- Critical decision-making
- Security and compliance
- Complex architecture
- Strategic planning

**Sonnet Agents (Balanced Performance)**
- Implementation tasks
- Documentation
- Process execution
- Operational work

### Agent Combinations

Common agent workflows:

**Data Quality Project:**
1. data-architect - Design data architecture
2. data-quality-analyst - Assess current state
3. data-engineer - Implement quality checks
4. data-quality-manager - Manage program

**Application Development:**
1. solution-architect-planner - Design architecture
2. implementation-developer - Write code
3. test-engineer-qa - Create tests
4. code-quality-reviewer - Review code
5. devops-deployment-specialist - Deploy

**Strategic Planning:**
1. /gensi - Launch GenSI workflow
2. Phases 0-6 execute automatically
3. strategic-planning-manager - Oversee execution

---

## Agent Selection Matrix

| Task Type | Recommended Agent(s) |
|-----------|---------------------|
| Code Review | code-quality-reviewer |
| New Feature | solution-architect-planner → implementation-developer → test-engineer-qa |
| Data Quality | data-quality-analyst → data-quality-manager |
| Compliance Check | compliance-officer |
| Strategic Planning | gensi-phase0-executor (via /gensi) |
| Documentation | technical-writer |
| UI Development | ui-ux-frontend-engineer |
| Cloud Deployment | devops-deployment-specialist |
| Financial Analysis | financial-analyst |
| Market Research | market-analysis-mgr |

---

## Version History

- **1.1.0** - Added 31 FBLAI agents (data quality, business, compliance, GenSI)
- **1.0.0** - Initial 9 OptivAI development agents

---

For detailed integration documentation, see [INTEGRATION-COMPLETE.md](INTEGRATION-COMPLETE.md).

For plugin installation and usage, see [README.md](README.md).
