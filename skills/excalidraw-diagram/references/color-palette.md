# Color Palette & Brand Style — GZ Dev OS

**This is the single source of truth for all colors and brand-specific styles.** To customize diagrams for your own brand, edit this file — everything else in the skill is universal.

---

## Shape Colors (Semantic — GZ Dev OS Layers)

Colors encode the 4-layer architecture and system semantics.

| Semantic Purpose | Fill | Stroke | Use For |
|------------------|------|--------|---------|
| Platform/Core | `#dbeafe` | `#1e3a5f` | devos_platform, governance, policy, telemetry |
| Workloads | `#d1fae5` | `#065f46` | reporting, queries, formatters, data contracts |
| Adapters | `#fef3c7` | `#b45309` | MCP server, transport layer |
| Gateway | `#e0e7ff` | `#3730a3` | FastAPI HTTP API |
| UI | `#fce7f3` | `#9d174d` | React frontend, Vite, Tailwind |
| AI/LLM | `#ddd6fe` | `#6d28d9` | AI agent, LLM calls, Claude |
| Infrastructure | `#f1f5f9` | `#475569` | Terraform, AWS, ECS, ALB |
| Error/Alert | `#fecaca` | `#b91c1c` | Failures, rollback, red lane |
| Success/Green | `#bbf7d0` | `#15803d` | CI pass, deploy success, green lane |
| Decision | `#fef3c7` | `#b45309` | Branch points, gates, yellow lane |
| Start/Trigger | `#fed7aa` | `#c2410c` | Entry points, user actions |
| Inactive/Disabled | `#dbeafe` | `#1e40af` | Pending features (use dashed stroke) |

**Rule**: Always pair a darker stroke with a lighter fill for contrast.

---

## Text Colors (Hierarchy)

Use color on free-floating text to create visual hierarchy without containers.

| Level | Color | Use For |
|-------|-------|---------|
| Title | `#1e40af` | Section headings, major labels |
| Subtitle | `#3b82f6` | Subheadings, secondary labels |
| Body/Detail | `#64748b` | Descriptions, annotations, metadata |
| On light fills | `#374151` | Text inside light-colored shapes |
| On dark fills | `#ffffff` | Text inside dark-colored shapes |

---

## Evidence Artifact Colors

Used for code snippets, data examples, and other concrete evidence inside technical diagrams.

| Artifact | Background | Text Color |
|----------|-----------|------------|
| Code snippet | `#1e293b` | Syntax-colored (language-appropriate) |
| JSON/data example | `#1e293b` | `#22c55e` (green) |
| SQL query | `#1e293b` | `#60a5fa` (blue) |

---

## Default Stroke & Line Colors

| Element | Color |
|---------|-------|
| Arrows | Use the stroke color of the source element's semantic purpose |
| Structural lines (dividers, trees, timelines) | `#1e3a5f` or `#64748b` (slate) |
| Marker dots (fill + stroke) | `#3b82f6` |

---

## Lane Colors (Change Classification)

For diagrams showing the lane-based change system:

| Lane | Fill | Stroke |
|------|------|--------|
| Green (safe) | `#bbf7d0` | `#15803d` |
| Yellow (review) | `#fef3c7` | `#b45309` |
| Red (restricted) | `#fecaca` | `#b91c1c` |

---

## Background

| Property | Value |
|----------|-------|
| Canvas background | `#ffffff` |
