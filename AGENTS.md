# AGENTS.md — OptivAI Claude Plugin

Reference material for agents (Claude Code, sub-agents, Pi workers) operating
inside this repository. Conventions in this file are enforced by hooks and
tests; deviation is a defect.

For interactive humans, see `README.md`. For broader product context, see
`CLAUDE.md`.

---

## Bead labeling convention (2026-06-03+)

**Rule.** Every bead created inside a git working tree carries a
`repo:<basename>` label, where `<basename>` is the directory name of the
repo root (e.g. `repo:optivai-claude-plugin`). Beads not created inside any
git tree carry no `repo:` label.

**Why.** One canonical beads database (`~/.beads/issues.jsonl`) is shared
across every repo on this machine. The label makes the source repo
recoverable as a filter on `beads list`, without polluting the bead ID
(which uses prefix `gz` / `fblai` / `optivai` for sequencing, not routing).

**Two surfaces enforce this:**

| Surface | When it fires |
|---|---|
| `scripts/shell-aliases.sh` → `beads-create` / `bc` | Interactive terminal (after one-time install in `~/.zshrc`) |
| `scripts/hooks/beads_writer.py` → `_apply_repo_label` | Reflexive — every auto-bead created from a tool-use or user-prompt event |

**Agent responsibility.** When you call `beads create` directly via the
Bash tool, neither surface above is in scope (the shell function is not
sourced, the hook only fires on Claude's PreToolUse / UserPromptSubmit
paths — not on agent-issued `beads create` invocations). You MUST follow
up with `beads label` immediately:

```bash
OUT=$(beads create "Your bead title")
ID=$(echo "$OUT" | grep -oE '\b(gz|fblai|optivai)-[a-z0-9]+\b' | head -1)
if REPO=$(git rev-parse --show-toplevel 2>/dev/null); then
    beads label "$ID" "repo:$(basename "$REPO")"
fi
```

`beads label` is idempotent — re-applying an existing label exits 0 with
an informational message. Always safe to call defensively.

**Cross-cutting work.** A bead that spans multiple repos carries one
`repo:` label per repo it touches. Example — the HARNESS-RECALL epic
covers both this plugin and the Pi plugin:

```bash
beads label gz-r6ivl "repo:optivai-claude-plugin"
beads label gz-r6ivl "repo:optivai-pi-plugin"
```

**Filter syntax.**

```bash
beads list -l repo:optivai-claude-plugin                 # single repo
beads list --status open -l repo:optivai-claude-plugin   # status + repo
beads list -l repo:optivai-claude-plugin -l repo:optivai-pi-plugin
                                                          # AND — cross-cutting only
```

Multi-`-l` is intersection, not union — beads must carry every label to
match. This is how cross-cutting epics surface.

**Tests.** The hook-side enforcement is covered by
`scripts/hooks/tests/test_beads_writer_repo_label.py` (7 tests; fail-open
contract verified for missing-git, label-CLI-failure, timeout, and
idempotency paths).

**Out of scope (for now).** Project-scoping (`proj:<name>`),
file-scoping (`file:<path>`), and agent-scoping (`agent:<name>`) are not
part of this convention. Add them as separate labels if needed; do not
overload `repo:`.

**Canonical reference.** This section is the source of truth. The
`/bead-create.md` slash command repeats the agent two-step pattern for
in-session lookup; `/bead-list.md` repeats the filter syntax. Other
`/bead-*.md` commands point back here.
