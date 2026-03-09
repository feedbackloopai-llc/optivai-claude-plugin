# Create PRP (Product Requirement Prompt)

Help the user create a comprehensive Product Requirement Prompt (PRP) for: $ARGUMENTS

## What is a PRP?

A PRP is PRD + curated codebase intelligence + agent runbook—the minimum viable packet an AI needs to ship production-ready code on the first pass.

## PRP Structure Template

```markdown
# PRP: [Feature/Product Name]

## 1. Overview
- **Purpose**: What problem does this solve?
- **Scope**: What's in/out of scope?
- **Success Criteria**: How do we know it's done?

## 2. Technical Context
- **Tech Stack**: Relevant technologies (PostgreSQL, Python, etc.)
- **Key Files**: Existing files to reference or modify
- **Patterns to Follow**: Existing code patterns to mirror
- **Dependencies**: External libraries or services required

## 3. Implementation Details
- **Data Flow**: How data moves through the system
- **API Contracts**: Input/output specifications
- **Schema Changes**: Database modifications needed
- **Error Handling**: How to handle failures

## 4. Constraints & Rules
- **Security**: Authentication, authorization requirements
- **Performance**: Response time, throughput requirements
- **Compliance**: Regulatory or business rules

## 5. Validation Criteria
- **Unit Tests**: What to test at unit level
- **Integration Tests**: Cross-system validation
- **Acceptance Criteria**: User-facing validation

## 6. External References
- **Documentation**: Links to relevant docs
- **Examples**: Similar implementations to reference
- **Research**: Web resources consulted
```

## Instructions for PRP Creation

Research and develop a complete PRP based on the feature/product description above. Follow these guidelines:

## Research Process

Begin with thorough research to gather all necessary context:

1. **Documentation Review**
   - Check for README.md, CLAUDE.md in the project root
   - Identify any documentation gaps that need to be addressed
   - Ask the user if additional documentation should be referenced

2. **Web Research**
   - Use web search to gather additional context
   - Research the concept of the feature/product
   - Look into library documentation
   - Look into example implementations on StackOverflow
   - Look into example implementations on GitHub
   - Ask the user if additional web search should be referenced

3. **Codebase Exploration**
   - Identify relevant files and directories that provide implementation context
   - Ask the user about specific areas of the codebase to focus on
   - Look for patterns that should be followed in the implementation

4. **FeedbackLoopAI-Specific Context**
   - Check PostgreSQL schema requirements
   - Confirm logging requirements (DataFixLog for data ops)
   - Review existing patterns in the codebase

5. **Implementation Requirements**
   - Confirm implementation details with the user
   - Ask about specific patterns or existing features to mirror
   - Inquire about external dependencies or libraries to consider

## Context Prioritization

A successful PRP must include comprehensive context through specific references to:

- Files in the codebase
- Web search results and URLs
- Documentation
- External resources
- Example implementations
- Validation criteria

## User Interaction

After completing initial research, present findings to the user and confirm:

- The scope of the PRP
- Patterns to follow
- Implementation approach
- Validation criteria

If the user answers with "continue", you are on the right path—continue with the PRP creation without additional user input.

## Output

Save the completed PRP to: `docs/prp-[feature-name].md` in the project directory.
