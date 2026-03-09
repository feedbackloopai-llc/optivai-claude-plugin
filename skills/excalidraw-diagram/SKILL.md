---
name: excalidraw-diagram
description: Create Excalidraw diagram JSON files that make visual arguments. Use when the user wants to visualize workflows, architectures, or concepts. Customized for OptivAI Dev OS — the 4-layer AI execution platform.
---

# Excalidraw Diagram Creator — OptivAI Dev OS

Generate `.excalidraw` JSON files that **argue visually**, not just display information.

**Setup:** If the user asks you to set up this skill (renderer, dependencies, etc.), see the First-Time Setup section under Render & Validate.

## OptivAI Dev OS Context

This skill operates within the OptivAI Dev OS repository. The system has a **4-layer architecture**:

```
PLATFORM  (devos_platform/devos/)    — governance, policy, telemetry, cost
WORKLOADS (workloads/reporting/)     — business logic, queries, data contracts
ADAPTERS  (adapters/mcp/)            — transport + interface (MCP server)
GATEWAY   (gateway/)                 — HTTP API for browser clients
UI        (ui/)                      — Vite + React + Tailwind browser client
```

### Key Architecture Docs (source material for diagrams)
- `docs/architecture/safe-change-engine.md` — Defect response, governance, deploy safety
- `docs/architecture/agent-pipeline.md` — AI dev pipeline + permission matrix
- `docs/architecture/mcp-ecosystem.md` — MCP as universal interface
- `docs/architecture/tool-contracts.md` — Universal tool contract + preview/commit

### Lane-Based Change Classification
| Lane | What Changes | Gate |
|---|---|---|
| Green | queries, formatters, tests, docs, UI | CI passes → auto-merge |
| Yellow | SQL, contracts, connections, adapters | CI + human approval |
| Red | platform core, prod terraform, IAM, security groups | CI + explicit human approval |

### Terraform Module Topology
Modules: ECR → ECS Service → ALB → Security Groups → IAM → CloudWatch. All resources prefixed `gz-devos-{resource}-{env}`.

**Output path**: All diagrams go to `docs/diagrams/` (PNG + source `.excalidraw` JSON).

## Customization

**All colors and brand-specific styles live in one file:** `references/color-palette.md`. Read it before generating any diagram and use it as the single source of truth for all color choices — shape fills, strokes, text colors, evidence artifact backgrounds, everything.

To make this skill produce diagrams in your own brand style, edit `color-palette.md`. Everything else in this file is universal design methodology and Excalidraw best practices.

---

## Core Philosophy

**Diagrams should ARGUE, not DISPLAY.**

A diagram isn't formatted text. It's a visual argument that shows relationships, causality, and flow that words alone can't express. The shape should BE the meaning.

**The Isomorphism Test**: If you removed all text, would the structure alone communicate the concept? If not, redesign.

**The Education Test**: Could someone learn something concrete from this diagram, or does it just label boxes? A good diagram teaches—it shows actual formats, real event names, concrete examples.

---

## Depth Assessment (Do This First)

Before designing, determine what level of detail this diagram needs:

### Simple/Conceptual Diagrams
Use abstract shapes when:
- Explaining a mental model or philosophy
- The audience doesn't need technical specifics
- The concept IS the abstraction (e.g., "separation of concerns")

### Comprehensive/Technical Diagrams
Use concrete examples when:
- Diagramming a real system, protocol, or architecture
- The diagram will be used to teach or explain (e.g., YouTube video)
- The audience needs to understand what things actually look like
- You're showing how multiple technologies integrate

**For technical diagrams, you MUST include evidence artifacts** (see below).

---

## Research Mandate (For Technical Diagrams)

**Before drawing anything technical, research the actual specifications.**

If you're diagramming a protocol, API, or framework:
1. Look up the actual JSON/data formats
2. Find the real event names, method names, or API endpoints
3. Understand how the pieces actually connect
4. Use real terminology, not generic placeholders

Bad: "Protocol" → "Frontend"
Good: "MCP tool call → preview_result → human approve → commit_result" with actual JSON shapes

**For OptivAI Dev OS diagrams**: Read the relevant architecture doc before designing. Use real module names, actual query paths, real Terraform resource names.

---

## Evidence Artifacts

Evidence artifacts are concrete examples that prove your diagram is accurate and help viewers learn. Include them in technical diagrams.

**Types of evidence artifacts** (choose what's relevant to your diagram):

| Artifact Type | When to Use | How to Render |
|---------------|-------------|---------------|
| **Code snippets** | APIs, integrations, implementation details | Dark rectangle + syntax-colored text (see color palette for evidence artifact colors) |
| **Data/JSON examples** | Data formats, schemas, payloads | Dark rectangle + colored text (see color palette) |
| **Event/step sequences** | Protocols, workflows, lifecycles | Timeline pattern (line + dots + labels) |
| **UI mockups** | Showing actual output/results | Nested rectangles mimicking real UI |
| **Real input content** | Showing what goes IN to a system | Rectangle with sample content visible |
| **API/method names** | Real function calls, endpoints | Use actual names from docs, not placeholders |

---

## Multi-Zoom Architecture

Comprehensive diagrams operate at multiple zoom levels simultaneously.

### Level 1: Summary Flow
A simplified overview showing the full pipeline or process at a glance. Often placed at the top or bottom of the diagram.

### Level 2: Section Boundaries
Labeled regions that group related components. For OptivAI Dev OS, natural section boundaries are the 4 layers: Platform, Workloads, Adapters, Gateway, UI.

### Level 3: Detail Inside Sections
Evidence artifacts, code snippets, and concrete examples within each section.

**For comprehensive diagrams, aim to include all three levels.**

---

## Container vs. Free-Floating Text

**Not every piece of text needs a shape around it.** Default to free-floating text. Add containers only when they serve a purpose.

| Use a Container When... | Use Free-Floating Text When... |
|------------------------|-------------------------------|
| It's the focal point of a section | It's a label or description |
| It needs visual grouping with other elements | It's supporting detail or metadata |
| Arrows need to connect to it | It describes something nearby |
| The shape itself carries meaning (decision diamond, etc.) | It's a section title, subtitle, or annotation |
| It represents a distinct "thing" in the system | It's a section title, subtitle, or annotation |

**Typography as hierarchy**: Use font size, weight, and color to create visual hierarchy without boxes. A 28px title doesn't need a rectangle around it.

---

## Design Process (Do This BEFORE Generating JSON)

### Step 0: Assess Depth Required
Before anything else, determine if this needs to be:
- **Simple/Conceptual**: Abstract shapes, labels, relationships
- **Comprehensive/Technical**: Concrete examples, code snippets, real data

**If comprehensive**: Read the relevant GZ architecture docs first.

### Step 1: Understand Deeply
Read the content. For each concept, ask:
- What does this concept **DO**? (not what IS it)
- What relationships exist between concepts?
- What's the core transformation or flow?
- **What would someone need to SEE to understand this?**

### Step 2: Map Concepts to Patterns
For each concept, find the visual pattern that mirrors its behavior:

| If the concept... | Use this pattern |
|-------------------|------------------|
| Spawns multiple outputs | **Fan-out** (radial arrows from center) |
| Combines inputs into one | **Convergence** (funnel, arrows merging) |
| Has hierarchy/nesting | **Tree** (lines + free-floating text) |
| Is a sequence of steps | **Timeline** (line + dots + free-floating labels) |
| Loops or improves continuously | **Spiral/Cycle** (arrow returning to start) |
| Is an abstract state or context | **Cloud** (overlapping ellipses) |
| Transforms input to output | **Assembly line** (before → process → after) |
| Compares two things | **Side-by-side** (parallel with contrast) |
| Separates into phases | **Gap/Break** (visual separation between sections) |

### Step 3: Ensure Variety
For multi-concept diagrams: **each major concept must use a different visual pattern**. No uniform cards or grids.

### Step 4: Sketch the Flow
Before JSON, mentally trace how the eye moves through the diagram. There should be a clear visual story.

### Step 5: Generate JSON
Only now create the Excalidraw elements. **See below for how to handle large diagrams.**

### Step 6: Render & Validate (MANDATORY)
After generating the JSON, you MUST run the render-view-fix loop until the diagram looks right. This is not optional — see the **Render & Validate** section below for the full process.

---

## Large / Comprehensive Diagram Strategy

**For comprehensive or technical diagrams, you MUST build the JSON one section at a time.** Do NOT attempt to generate the entire file in a single pass. This is a hard constraint — Claude Code has a ~32,000 token output limit per response, and a comprehensive diagram easily exceeds that in one shot.

### The Section-by-Section Workflow

**Phase 1: Build each section**

1. **Create the base file** with the JSON wrapper (`type`, `version`, `appState`, `files`) and the first section of elements.
2. **Add one section per edit.** Each section gets its own dedicated pass.
3. **Use descriptive string IDs** (e.g., `"platform_rect"`, `"arrow_to_workloads"`) so cross-section references are readable.
4. **Namespace seeds by section** (e.g., section 1 uses 100xxx, section 2 uses 200xxx) to avoid collisions.
5. **Update cross-section bindings** as you go.

**Phase 2: Review the whole**

After all sections are in place, read through the complete JSON and check:
- Are cross-section arrows bound correctly on both ends?
- Is the overall spacing balanced?
- Do IDs and bindings all reference elements that actually exist?

**Phase 3: Render & validate**

Now run the render-view-fix loop from the Render & Validate section.

### What NOT to Do

- **Don't generate the entire diagram in one response.**
- **Don't use a coding agent** to generate the JSON.
- **Don't write a Python generator script.**

---

## Visual Pattern Library

### Fan-Out (One-to-Many)
Central element with arrows radiating to multiple targets. Use for: sources, PRDs, root causes, central hubs.

### Convergence (Many-to-One)
Multiple inputs merging through arrows to single output. Use for: aggregation, funnels, synthesis.

### Tree (Hierarchy)
Parent-child branching with connecting lines and free-floating text (no boxes needed).

### Spiral/Cycle (Continuous Loop)
Elements in sequence with arrow returning to start. Use for: feedback loops, iterative processes.

### Cloud (Abstract State)
Overlapping ellipses with varied sizes. Use for: context, memory, conversations, mental states.

### Assembly Line (Transformation)
Input → Process Box → Output with clear before/after.

### Side-by-Side (Comparison)
Two parallel structures with visual contrast.

### Gap/Break (Separation)
Visual whitespace or barrier between sections.

### Lines as Structure
Use lines (type: `line`, not arrows) as primary structural elements instead of boxes:
- **Timelines**: Vertical or horizontal line with small dots at intervals
- **Tree structures**: Vertical trunk line + horizontal branch lines
- **Dividers**: Thin dashed lines to separate sections

---

## Shape Meaning

| Concept Type | Shape | Why |
|--------------|-------|-----|
| Labels, descriptions, details | **none** (free-floating text) | Typography creates hierarchy |
| Markers on a timeline | small `ellipse` (10-20px) | Visual anchor, not container |
| Start, trigger, input | `ellipse` | Soft, origin-like |
| End, output, result | `ellipse` | Completion, destination |
| Decision, condition | `diamond` | Classic decision symbol |
| Process, action, step | `rectangle` | Contained action |
| Abstract state, context | overlapping `ellipse` | Fuzzy, cloud-like |

**Rule**: Default to no container. Add shapes only when they carry meaning. Aim for <30% of text elements to be inside containers.

---

## Color as Meaning

Colors encode information, not decoration. Every color choice should come from `references/color-palette.md` — the semantic shape colors, text hierarchy colors, and evidence artifact colors are all defined there.

**Do not invent new colors.** If a concept doesn't fit an existing semantic category, use Primary/Neutral.

---

## Modern Aesthetics

- `roughness: 0` — Default for clean, professional diagrams
- `strokeWidth: 2` — Standard for shapes and primary arrows
- `opacity: 100` — Always, for all elements
- Small marker dots (10-20px ellipses) instead of full shapes where possible

---

## Layout Principles

- **Hero**: 300×150 — visual anchor, most important
- **Primary**: 180×90
- **Secondary**: 120×60
- **Small**: 60×40
- The most important element has the most whitespace around it (200px+)
- Flow: left→right or top→bottom for sequences, radial for hub-and-spoke
- If A relates to B, there must be an arrow

---

## Text Rules

**CRITICAL**: The JSON `text` property contains ONLY readable words.

```json
{
  "id": "myElement1",
  "text": "Start",
  "originalText": "Start"
}
```

Settings: `fontSize: 16`, `fontFamily: 3`, `textAlign: "center"`, `verticalAlign: "middle"`

---

## JSON Structure

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [...],
  "appState": {
    "viewBackgroundColor": "#ffffff",
    "gridSize": 20
  },
  "files": {}
}
```

## Element Templates

See `references/element-templates.md` for copy-paste JSON templates for each element type. Pull colors from `references/color-palette.md` based on each element's semantic purpose.

---

## Render & Validate (MANDATORY)

You cannot judge a diagram from JSON alone. After generating or editing the Excalidraw JSON, you MUST render it to PNG, view the image, and fix what you see — in a loop until it's right.

### How to Render

```bash
cd .claude/skills/excalidraw-diagram/references && uv run python render_excalidraw.py <path-to-file.excalidraw>
```

This outputs a PNG next to the `.excalidraw` file. Then use the **Read tool** on the PNG to actually view it.

### The Loop

After generating the initial JSON, run this cycle:

**1. Render & View** — Run the render script, then Read the PNG.

**2. Audit against your original vision** — Compare the rendered result to what you designed in Steps 1-4. Ask:
- Does the visual structure match the conceptual structure you planned?
- Does each section use the pattern you intended?
- Does the eye flow through the diagram in the order you designed?
- Is the visual hierarchy correct?
- For technical diagrams: are the evidence artifacts readable?

**3. Check for visual defects:**
- Text clipped by or overflowing its container
- Text or shapes overlapping other elements
- Arrows crossing through elements instead of routing around them
- Arrows landing on the wrong element or pointing into empty space
- Uneven spacing between elements
- Text too small to read at the rendered size
- Unbalanced composition

**4. Fix** — Edit the JSON to address everything you found.

**5. Re-render & re-view** — Run the render script again and Read the new PNG.

**6. Repeat** — Keep cycling until the diagram passes both the vision check and the defect check. Typically takes 2-4 iterations.

### When to Stop

The loop is done when:
- The rendered diagram matches the conceptual design
- No text is clipped, overlapping, or unreadable
- Arrows route cleanly and connect to the right elements
- Spacing is consistent and the composition is balanced

### First-Time Setup
If the render script hasn't been set up yet:
```bash
cd .claude/skills/excalidraw-diagram/references
uv sync
uv run playwright install chromium
```

---

## Quality Checklist

### Depth & Evidence (Technical Diagrams)
1. **Research done**: Did you read the relevant GZ architecture docs?
2. **Evidence artifacts**: Are there code snippets, JSON examples, or real data?
3. **Multi-zoom**: Does it have summary flow + section boundaries + detail?
4. **Concrete over abstract**: Real content shown, not just labeled boxes?

### Conceptual
5. **Isomorphism**: Does each visual structure mirror its concept's behavior?
6. **Argument**: Does the diagram SHOW something text alone couldn't?
7. **Variety**: Does each major concept use a different visual pattern?

### Container Discipline
8. **Minimal containers**: Could any boxed element work as free-floating text instead?
9. **Lines as structure**: Are tree/timeline patterns using lines + text rather than boxes?
10. **Typography hierarchy**: Are font size and color creating visual hierarchy?

### Structural
11. **Connections**: Every relationship has an arrow or line
12. **Flow**: Clear visual path for the eye to follow
13. **Hierarchy**: Important elements are larger/more isolated

### Technical
14. **Text clean**: `text` contains only readable words
15. **Font**: `fontFamily: 3`
16. **Roughness**: `roughness: 0`
17. **Opacity**: `opacity: 100` for all elements

### Visual Validation (Render Required)
18. **Rendered to PNG**: Diagram has been rendered and visually inspected
19. **No text overflow**: All text fits within its container
20. **No overlapping elements**: Shapes and text don't overlap
21. **Arrows land correctly**: Arrows connect to intended elements
22. **Balanced composition**: No large empty voids or overcrowded regions
