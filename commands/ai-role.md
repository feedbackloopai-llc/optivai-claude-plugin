# AI Role Command

**Command:** `/ai-role [role-name]`

**Description:** Activate an AI role expert to assist with specialized tasks

## Usage

This command dynamically activates AI expert roles by loading role-specific instructions from the FBLAI knowledge base.

### Syntax
```
/ai-role [role-name]
```

### Parameters
- **role-name** (optional):
  - A role name from the registry (reference name or full title)
  - If no parameter is supplied, the help system will display available roles

## Implementation

When this command is executed, Claude Code MUST:

1. **Registry Memory Check** (ALWAYS FIRST):
   - Check if the AI role registry is already in memory from a previous invocation
   - If NOT in memory, read `~/.claude/instructions/global/ai-role-registry.md` and load it into memory
   - If already in memory, proceed without re-reading the registry file
   - The registry should remain in memory for the entire session unless memory constraints require clearing
   - **This step MUST complete before proceeding to parameter checks**

2. **Parameter Check**:
   - If no parameter is provided, invoke the help system (registry is already loaded from step 1)
   - If parameter is provided, proceed to step 3

3. **Parameter Validation**: Check if the provided parameter matches either a reference name or title from the in-memory registry (loaded in step 1)

4. **Error Handling**: If no match is found, display the help information showing available roles from the in-memory registry

5. **Role Activation**: If a match is found, load the corresponding AI role markdown file from `~/.claude/instructions/ai-roles/[reference-name].md`

6. **Context Setting**: Apply the role's expertise and behavioral guidelines

7. **Ready State**: The AI assistant operates with the specialized role's capabilities

## Help System

The help system is invoked when:
- No parameters are provided
- An invalid role name is provided (not in registry)

When invoked, the system will:
1. Use the in-memory registry (loading it first if not already loaded)
2. Display command syntax for role activation
3. Show all available AI role titles from the in-memory registry
4. Provide usage examples
5. Reference the AI Role Registry for detailed descriptions

### Example Help Output
```
AI Role Command - Activate specialized AI expert roles

Syntax:
  /ai-role [role-name]

Available Roles:
  - Business Analyst
  - Change Management Specialist
  - Compliance Officer
  - Data Architect
  - Data Engineer
  - Data Governance Lead
  - Data Quality Analyst
  - Data Quality Manager
  - Data Scientist
  - Data Steward
  - Database Administrator
  - Immigration Law Subject Matter Expert
  - Machine Learning Engineer
  - Market Analysis Manager Expert
  - Project Manager
  - Senior Software Engineer
  - Subject Matter Expert
  - User Research Expert

Examples:
  /ai-role data-quality-analyst
  /ai-role "Senior Software Engineer"
  /ai-role market-analysis-mgr

For detailed role descriptions, see: ~/.claude/instructions/global/ai-role-registry.md
```

## Role Matching Logic

The system performs case-insensitive matching against the in-memory registry:
1. First checks for exact match with reference name from the in-memory registry
2. Then checks for exact match with title from the in-memory registry
3. If no match is found, displays the help information with current registry contents

## Role Independence

AI roles are independent entities that:
- Can reference LLM instruction files but are not required to
- Contain self-contained role definitions and expertise areas
- May optionally include references to supplementary instruction files
- Operate autonomously without mandatory file dependencies

## Instruction File Integration

While not required, AI roles may reference instruction files from the library:
- Market research instruction files for analytical methodologies
- Documentation style guides for formatting standards
- Security practices for audit procedures
- Development standards for code review criteria

The role files themselves determine which (if any) instruction files to reference.

## Registry Management

**IMPORTANT**: This command file does not maintain a static list of roles. The authoritative source for all available AI roles is:
- `~/.claude/instructions/global/ai-role-registry.md` - The complete, maintained registry of all AI roles

### Memory Optimization
- The registry is loaded into memory on first invocation and retained for the entire session
- This prevents redundant file reads and improves performance
- If memory constraints are encountered and the registry is no longer in memory, it will be automatically reloaded on the next invocation
- The in-memory registry ensures consistent and fast access to role information throughout the session

## Managing AI Roles (Developers Only)

**Note**: Creating, updating, and deleting AI roles is done using developer tooling, not this command.

For developers working in the repository:
- **Create new role**: `npm run dev:create-role "Role Name"` or use VS Code Task
- **Delete existing role**: `npm run dev:delete-role "role-name"` or use VS Code Task
- **Validate roles**: `npm run dev:validate`

See `.dev/docs/developer-guide.md` for complete documentation.

## Related Resources

- `~/.claude/instructions/global/ai-role-registry.md` - Complete registry of all AI roles
- `~/.claude/instructions/ai-roles/` - Individual role files containing the actual role definitions
- `.dev/docs/ai-role-creation-standards.md` - Standardized 7-section format requirements (developers only)
- `.dev/docs/developer-guide.md` - Developer tooling usage guide (developers only)
