---
name: tana
description: Use when creating, reading, editing, moving, or searching Tana nodes — covers workspace selection, calendar daily notes, meeting creation, tag schemas, import format, and search query syntax. Required before ANY Tana MCP tool call.
---

# Tana MCP Tools

Reference for the Tana MCP tool suite. These are registered tools — call them directly, not via bash.

## Critical Rules

1. **Always identify the correct workspace first.** Call `tana_workspaces` at session start.
2. **FeedbackLoopAI work → FeedbackLoopAI Tracking - CH workspace** (`tuswHkOw_qk9`). Never put work content in the Home workspace.
3. **Verify the current date with the user or Tana** — do NOT rely on the system clock. Use `tana_calendar_node` without a date to get today, or confirm with the user.
4. **Get tag schema before creating tagged nodes.** Always call `tana_tag_schema` before `tana_import` with tags.

## Workspace Reference

| Workspace | ID | Use For |
|-----------|-----|---------|
| Home | `R7ib_GU4nYIy` | Personal, non-work |
| FeedbackLoopAI Tracking - CH | `tuswHkOw_qk9` | All FeedbackLoopAI/client work |

**Always confirm workspace IDs with `tana_workspaces` — IDs may change.**

## Daily Notes Pattern

To add content to a workspace's daily notes for a specific date:

```
1. tana_calendar_node(workspaceId, granularity="day", date="YYYY-MM-DD")
   → Returns nodeId for that day
2. tana_import(parentNodeId=<day_node>, content=<tana paste>)
   → Content appears under that day's daily notes
```

**If date is omitted**, `tana_calendar_node` returns today's node.

## Creating Meetings

Full workflow:

```
1. tana_workspaces          → get workspace ID
2. tana_list_tags           → find Meeting tag ID
3. tana_tag_schema(tagId)   → get field IDs (Date, Attendees, Time)
4. tana_calendar_node       → get day node for target date
5. tana_import              → create meeting under day node
```

### Meeting Import Format (Tana Paste)

```
- Meeting Title #[[^<meeting_tag_id>]]
  - [[^<date_field_id>]]:: [[date:YYYY-MM-DD]]
  - [[^<attendees_field_id>]]:: [[^<person_node_id>]]
  - [[^<time_field_id>]]:: 11:30 AM ET
  - Notes go here as children
    - Nested children use 2-space indent
```

**Key syntax:**
- `#[[^tagId]]` — apply a supertag
- `[[^fieldId]]:: value` — set a field value
- `[[^nodeId]]` — reference an existing node
- `[[date:YYYY-MM-DD]]` — date literal
- `[ ]` / `[x]` — checkbox unchecked/checked
- 2-space indentation for hierarchy

## Search Queries — ⚠️ Common Failure Point

The `tana_search` `query` parameter must be a **JSON object**. It is NOT a string.

### Working Query Patterns

**Text search:**
```json
{"textContains": "search term"}
```

**By supertag:**
```json
{"hasType": "<tagId>"}
```

**Combined (AND):**
```json
{"and": [{"hasType": "<tagId>"}, {"textContains": "term"}]}
```

**By creation date (last N days):**
```json
{"created": {"last": 7}}
```

**Combined tag + date:**
```json
{"and": [{"hasType": "<tagId>"}, {"created": {"last": 30}}]}
```

### Known Tag IDs

| Tag | ID |
|-----|-----|
| Meeting | `8Cb232uhOIT9` |
| Action Item | `6YxAzWKrARKp` |
| Task | `4BUBqVWYPV_B` |
| Person | `QLHQs9FofjcB` |
| Client | `Cp5vxDv4X4pf` |
| Organization | `dkPoIas1NPzQ` |
| Code | `qUeH2vWTqKUa` |

**Always verify with `tana_list_tags` — IDs may change.**

## Key People / Client Node IDs

| Node | ID | Notes |
|------|-----|-------|
| NCCDP (Client) | `TMQWTEjGRN8s` | Use as attendee ref |

**Find person/client nodes:** Search by tag, or read existing meeting nodes to find reference IDs.

## Move Operations

`tana_move_node(nodeId, targetNodeId, position)` — moves across workspaces.

- `position`: `"start"`, `"end"` (default), `"after"`, `"before"`
- `"after"` / `"before"` require `referenceNodeId`
- Moving from Home workspace to GZ workspace works — both use node IDs

## Quick Reference — All Tools

| Tool | Purpose |
|------|---------|
| `tana_workspaces` | List workspaces + IDs |
| `tana_list_tags` | List supertags in a workspace |
| `tana_tag_schema` | Get fields/types for a tag — **call before import** |
| `tana_search` | Search nodes — query must be JSON object |
| `tana_read_node` | Read node + children (maxDepth 0-10, default 1) |
| `tana_get_children` | Paginated children list |
| `tana_import` | Create nodes using Tana Paste format |
| `tana_edit_node` | Search-and-replace on node name/description |
| `tana_move_node` | Move node to new parent |
| `tana_tag` | Add/remove supertags from a node |
| `tana_set_field` | Set text/number/date/url field value |
| `tana_set_field_option` | Set dropdown/option field value |
| `tana_check` / `tana_uncheck` | Toggle checkbox |
| `tana_trash` | Soft delete (recoverable) |
| `tana_calendar_node` | Get/create day/week/month/year node |
| `tana_create_tag` | Create new supertag |
| `tana_add_field` | Add field to a tag |
| `tana_open` | Open node in Tana desktop app |

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Search query as string | Must be JSON object: `{"textContains": "term"}` |
| Wrong workspace for daily notes | Always use GZ workspace for work content |
| Wrong date on calendar node | Verify date with user — don't trust system clock |
| Import without tag schema | Call `tana_tag_schema` first to get field IDs |
| Assuming node IDs are stable | Re-verify IDs with `tana_list_tags` or `tana_search` per session |
| Moving nodes between workspaces accidentally | Always check source/target workspace before move |
