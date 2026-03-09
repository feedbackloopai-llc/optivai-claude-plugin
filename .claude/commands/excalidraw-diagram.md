# Excalidraw Diagram Skill

Create Excalidraw diagram JSON files that make visual arguments — architecture diagrams, workflows, concept maps. Customized for GZ Dev OS with semantic color palette.

## Setup

Before using this skill, read these reference files in order:

1. `skills/excalidraw-diagram/SKILL.md` — Full methodology: design process, visual patterns, quality checklist
2. `skills/excalidraw-diagram/references/color-palette.md` — **Single source of truth** for all colors (shape fills/strokes, text hierarchy, evidence artifacts, lane colors)
3. `skills/excalidraw-diagram/references/element-templates.md` — Copy-paste JSON templates for every element type
4. `skills/excalidraw-diagram/references/json-schema.md` — Excalidraw element properties reference

## Quick Reference

### Design Process
1. **Read the color palette first.** Every color comes from `color-palette.md`. Don't invent colors.
2. **Plan before JSON.** Map concepts to visual patterns (fan-out, timeline, side-by-side, assembly line).
3. **Build section by section.** Create base file with Section 1, then Edit to append subsequent sections. Do NOT generate entire diagram in one pass.
4. **Namespace seeds by section** (100xxx, 200xxx, 300xxx).
5. **Use descriptive string IDs** (`"orchestrator_rect"`, `"arrow_dm_to_orch"`).

### JSON Essentials
- Every element: `roughness: 0`, `opacity: 100`, `fontFamily: 3`
- Text inside shapes: set `containerId` on text, add `boundElements` on parent shape
- Free-floating text (titles, annotations): `containerId: null` — default to this
- Evidence artifacts (code/data): dark bg `#1e293b`, green text `#22c55e`
- Arrows: need `startBinding`/`endBinding` with `"focus": 0, "gap": 2`

### Render & Validate (Mandatory)
```bash
cd skills/excalidraw-diagram/references && uv run python render_excalidraw.py /path/to/diagram.excalidraw
```
Read the output PNG, audit, fix issues, re-render. Typically 2-4 iterations.

**First-time setup:**
```bash
cd skills/excalidraw-diagram/references && uv sync && uv run playwright install chromium
```

### Layout Defaults
- ~1000px wide, UI layer left edge at x=130, layer labels at x=30
- 55-70px height for boxes, 40-60px for utility boxes
- 13-14px font for content, 12px annotations, 22-28px titles
- 80-90px vertical spacing between layers

## Diagram Request: $ARGUMENTS

Read the reference files listed above, then create the requested diagram following the SKILL.md design process. Output to `docs/diagrams/` as both `.excalidraw` source and rendered `.png`.
