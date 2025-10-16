# Prime Agent - Optimized Context Command

**Purpose**: Prime any agent with complete OptivAI context for immediate productivity
**Usage**: `/prime-agent`

## Prime
Execute the 'Run', 'Read', and 'Report' sections to understand the codebase then summarize your findings

## Run
```bash
git status && git log --oneline -5 && git ls-files | grep -E '\.(py|ts|tsx|md)$' | head -20
```

## Read
You are now working on **OptivAI**, an enterprise Business Cloud SaaS platform that transforms business ideas into structured artifacts through LLM-driven ideation with AWS-powered automation.
User is using windows 11 with Powershell 7 - please prefer ps1 based scripts when possible however bash of course works and is an option.

### Essential Context Files (Read in Priority Order)

#### 1. Project Overview & Architecture
- **README.md** - Complete project overview, architecture, and current status
- **docs/CLAUDE WORKING/Working_Memory.md** - Recent work, current priorities, and session outcomes

#### 2. Development Standards & Organization
- **FILE_ORGANIZATION_RULES.md** - Critical file placement rules (NEVER create files in root)
- **Coding Rules.md** - Development standards, testing requirements, and constraints
- **CLAUDE.local.md** - Private project configuration and environment setup

#### 3. Current System Architecture
- **graphql_v2_server.py** - Main V2 server implementation (PRIMARY production server)
- **docs/api/API_MAPPING_DOCUMENT.md** - Complete V2 API endpoint reference
- **TESTING_RUNBOOK.md** - Testing procedures and infrastructure

### System Status Summary (September 2025)

**🎯 CURRENT STATE**: Production-ready enterprise SaaS with 100% V2 migration complete

#### Architecture Overview
- **Backend**: FastAPI V2 GraphQL Server (Port 8002) - 110 endpoints operational
- **Frontend**: React + TypeScript + Zustand (Port 3000 dev / 80 prod)
- **Auth**: AWS Cognito with UUID-based user identification (COMPLETED)
- **Storage**: V2 API Key Management with enterprise encryption
- **Infrastructure**: Docker for services, LocalStack for development

#### Critical System Changes
1. **UUID Authentication**: Migrated from PostgreSQL integers to Cognito `sub` UUIDs
2. **V2-Only Architecture**: Legacy DataStore completely removed
3. **Enterprise Encryption**: Production-ready envelope encryption with AWS KMS
4. **Performance Optimized**: 75% improvement in API response times
5. **Testing Infrastructure**: Completely rebuilt and validated

#### Environment Setup
```bash
# Backend Server (V2 Production System)
python graphql_v2_server.py

# Frontend Development
cd confluence-mcp-frontend && npm run dev

# Infrastructure Services
docker-compose -f docker-compose.dev.yml up -d
```

#### Authentication & Test Credentials
```bash
# AWS Cognito Configuration (us-east-2)
AWS_COGNITO_USER_POOL_ID=us-east-2_Lo3nCCMOu
AWS_COGNITO_CLIENT_ID=5kv4juhci59rerrs7dm1pqccor

# Test User (from login_test.json)
Username: your-test-username@example.com
Password: your-test-password-here
Cognito UUID: your-cognito-uuid-here

# Valid API Keys for Testing
ANTHROPIC_API_KEY="your-anthropic-api-key-here"
```

#### Service URLs
- **V2 GraphQL Server**: http://localhost:8002 (PRIMARY)
- **Frontend**: http://localhost:3000 (dev) | http://localhost (docker)
- **API Documentation**: http://localhost:8002/docs
- **Health Check**: http://localhost:8002/health

### Development Workflow Patterns

#### Current Branch Strategy
- **Main Branch**: `main` (stable production)
- **Development Branch**: `Enterprise-Cloud-SaaS` (current active)
- **Feature Workflow**: Branch from main/Enterprise-Cloud-SaaS → PR → merge

#### Key Constraints & Rules
- **NEVER create files in root directory** (follow FILE_ORGANIZATION_RULES.md)
- **Use timestamps in generated filenames** for reports/artifacts
- **Maintain AWS Cognito authentication flow** (no development fallbacks)
- **Update Working_Memory.md** after each work session
- **TDD Required**: All code changes need comprehensive tests

#### Testing Strategy (Updated October 2025)
 
 **Local Comprehensive Testing** (Primary Method - Mirrors GitHub Actions):

# Run full 4-job test suite locally with live output
python tools/scripts/run_comprehensive_tests_local.py

# Or use convenience scripts
.\run_tests_local.ps1  # PowerShell (recommended)
run_tests_local.bat    # Batch file

# Output streams to: docs/Doctor_Strange/ConsoleOutput.md

**4 Jobs Executed**:
1. backend-unit-tests - Unit tests with coverage
2. frontend-tests - Linting, type-check, build, tests
3. integration-tests - Integration tests (requires Docker services)
4. e2e-tests - End-to-end tests (requires all services + servers)

**Job-Specific Requirements**:
- Jobs 1-2: No services required
- Job 3: Requires Docker services (PostgreSQL, Redis, LocalStack, OpenSearch)
- Job 4: Requires all services + backend + frontend servers

**Quick Tests** (Individual jobs):
# Backend unit tests only
pytest tests/unit/ -v

# Integration tests (no services needed - e2e excluded)
pytest tests/integration/ tests/critical/ -m "not e2e" -v

# Frontend tests only
cd confluence-mcp-frontend && npm run test:coverage

# E2E tests only (requires all services)
pytest tests/ -m "e2e" -v

# Output written to: docs/Doctor_Strange/ConsoleOutput.md

### Business Context
**PROJECT PURPOSE**: Enterprise SaaS enabling LLM-driven business artifact generation with persistent JIRA/Confluence integration using guard-rail driven approach.

**VALUE PROPOSITION**: Transform business ideas into structured documents through intelligent LLM interaction with enterprise-grade security, AWS integration, and scalable architecture.

## Report
After reading the essential files, provide a structured summary covering:

1. **Architecture Understanding**: V2 system, authentication flow, and key components
2. **Current Development State**: Recent sessions, completed migrations, and current priorities
3. **Immediate Capabilities**: What you can help with based on the current codebase
4. **Key Constraints**: Critical rules and limitations to observe
5. **Next Actions**: Based on Working_Memory.md, what are the logical next steps

**Note**: This is a production enterprise system with zero tolerance for shortcuts. Always follow established patterns and maintain professional cloud SaaS standards.