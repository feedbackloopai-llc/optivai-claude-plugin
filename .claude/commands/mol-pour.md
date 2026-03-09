# Mol Pour - Instantiate Molecule Workflow

**Purpose**: Instantiate a molecule (workflow template) creating child beads with auto-wired dependencies
**Usage**: `/mol-pour <molecule-id> <parent-id> [--var key=value ...]`

## Instantiate Molecule

```bash
# Pour molecule onto parent bead
beads pour gz-mol123 gz-parent456
```

## With Template Variables

```bash
# Use context variables to customize step titles
beads pour gz-mol123 gz-parent456 --var entity_name=User --var api_version=v2
```

## Example Workflow

### 1. Create a Molecule Template

```bash
# Create the molecule
beads create "API Implementation Workflow" --type molecule
```

Then update its description with step definitions:

```markdown
## Step: design
Design the {{entity_name}} API schema
Tier: haiku

## Step: implement
Implement {{entity_name}} endpoints
Needs: design
Tier: sonnet

## Step: test
Write tests for {{entity_name}} API
Needs: implement
Tier: haiku

## Step: document
Document the {{entity_name}} API
Needs: test
Tier: haiku
```

### 2. Create Parent Work Item

```bash
beads create "Build User API" --type feature
```

### 3. Pour the Molecule

```bash
beads pour gz-mol123 gz-parent456 --var entity_name=User
```

This creates 4 child beads:
- "Design the User API schema" (ready)
- "Implement User endpoints" (blocked by design)
- "Write tests for User API" (blocked by implement)
- "Document the User API" (blocked by test)

### 4. Work Through the Steps

```bash
# Check what's ready
beads ready  # Shows: design step

# Complete design
beads update gz-design --status done

beads ready  # Now shows: implement step
```

## Step Definition Format

In molecule description:

```markdown
## Step: <ref>
<instructions>

Needs: <comma-separated refs>
Tier: <haiku|sonnet|opus>
Type: <task|wait>
WaitsFor: <comma-separated conditions>
```

## Reusable Patterns

Molecules are reusable templates:
- Create once, pour many times
- Different context variables = different instances
- Same dependency structure, different content

## Related Commands

- `/bead-create` - Create molecule template
- `/bead-ready` - See ready steps after pour
- `/bead-link` - Manual dependency linking
- `/bead-show` - View step details
