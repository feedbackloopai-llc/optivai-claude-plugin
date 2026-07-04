#!/usr/bin/env bash
# install-check.sh — Repo-vs-live install drift checker
#
# Compares the claude-plugin repo's installable files against the live
# ~/.claude/ install. Detects when the live install has evolved ahead of the
# repo (LIVE-NEWER — the silent killer that caused context_primer.py to be
# 2 months stale in the repo) and when the repo is ahead of live (REPO-NEWER).
#
# Usage:
#   ./scripts/install-check.sh [--strict] [--pull-live] [repo_root] [live_root]
#
#   --strict      Exit non-zero if ANY drift exists (for CI gating).
#                 By default exits 0 even when drift is found (report-only mode).
#
#   --pull-live   Copy LIVE-NEWER files back into the repo so live evolution
#                 gets captured. Only copies when live is newer AND content
#                 differs. Prints each copy. (Explicit operator action — not
#                 auto-run. This is how T3.4 reconciles the LIVE-NEWER list.)
#
#   repo_root     Path to optivai-claude-plugin root.
#                 Default: $OPTIVAI_CLAUDE_PLUGIN_ROOT or ~/dev/optivai-claude-plugin
#
#   live_root     Path to the live ~/.claude/ directory.
#                 Default: $OPTIVAI_CLAUDE_LIVE_ROOT or ~/.claude
#
# Exit codes:
#   0  all in sync, or drift found but not in --strict mode
#   1  drift found AND --strict mode, OR --pull-live found files to copy
#      (use the exit code to distinguish: --strict exits 1 on any drift;
#       report-only mode always exits 0)
#   2  repo root or live root does not exist

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
STRICT=false
PULL_LIVE=false
POSITIONAL=()

for arg in "$@"; do
    case "$arg" in
        --strict)    STRICT=true ;;
        --pull-live) PULL_LIVE=true ;;
        -*)          echo "Unknown flag: $arg" >&2; exit 1 ;;
        *)           POSITIONAL+=("$arg") ;;
    esac
done

REPO_ROOT="${POSITIONAL[0]:-${OPTIVAI_CLAUDE_PLUGIN_ROOT:-$HOME/dev/optivai-claude-plugin}}"
LIVE_ROOT="${POSITIONAL[1]:-${OPTIVAI_CLAUDE_LIVE_ROOT:-$HOME/.claude}}"

if [ ! -d "$REPO_ROOT" ]; then
    echo "install-check: repo root not found: $REPO_ROOT" >&2
    echo "  Set OPTIVAI_CLAUDE_PLUGIN_ROOT or pass it as the first positional argument." >&2
    exit 2
fi

if [ ! -d "$LIVE_ROOT" ]; then
    echo "install-check: live root not found: $LIVE_ROOT" >&2
    echo "  Set OPTIVAI_CLAUDE_LIVE_ROOT or pass it as the second positional argument." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Data-driven file map
# Each entry is a pair of consecutive elements:
#   repo_path_relative_to_repo_root
#   live_path_relative_to_live_root
#
# Paths use forward slashes; no leading slash.
# To add a file: add two lines (repo_rel, live_rel).
# ---------------------------------------------------------------------------
FILE_MAP=(
    # Hook scripts installed from scripts/hooks/ → hooks/
    "scripts/hooks/log_writer.py"            "hooks/log_writer.py"
    "scripts/hooks/pre-tool-use.py"          "hooks/pre-tool-use.py"
    "scripts/hooks/user-prompt-submit.py"    "hooks/user-prompt-submit.py"
    "scripts/hooks/memory_writer.py"         "hooks/memory_writer.py"
    "scripts/hooks/subagent_context.py"      "hooks/subagent_context.py"
    "scripts/hooks/context_primer.py"        "hooks/context_primer.py"
    "scripts/hooks/beads_writer.py"          "hooks/beads_writer.py"
    "scripts/hooks/redact_secrets.py"        "hooks/redact_secrets.py"
    "scripts/hooks/session_summary.py"       "hooks/session_summary.py"
    "scripts/hooks/stop-hook.sh"             "hooks/stop-hook.sh"
    "scripts/hooks/stop-hook.py"             "hooks/stop-hook.py"
    "scripts/hooks/brain_hook.py"            "hooks/brain_hook.py"
    "scripts/hooks/till_done.py"             "hooks/till_done.py"
    "scripts/hooks/auto_recall_hook.py"      "hooks/auto_recall_hook.py"

    # open_brain.py lives at scripts/ (not hooks/) but installs to hooks/
    "scripts/open_brain.py"                  "hooks/open_brain.py"

    # Additional scripts that end up in hooks/ (installed via upgrade/migrate paths)
    "scripts/citation_walker.py"             "hooks/citation_walker.py"
    "scripts/vf_probe.py"                    "hooks/vf_probe.py"
    "scripts/time_travel.py"                 "hooks/time_travel.py"

    # redact/ package — repo: scripts/redact/  live: hooks/redact/
    "scripts/redact/__init__.py"                        "hooks/redact/__init__.py"
    "scripts/redact/compose.py"                         "hooks/redact/compose.py"
    "scripts/redact/default_pipeline.py"                "hooks/redact/default_pipeline.py"
    "scripts/redact/regex_redactor.py"                  "hooks/redact/regex_redactor.py"
    "scripts/redact/types.py"                           "hooks/redact/types.py"
    "scripts/redact/recognizers/__init__.py"            "hooks/redact/recognizers/__init__.py"
    "scripts/redact/recognizers/context.py"             "hooks/redact/recognizers/context.py"
    "scripts/redact/recognizers/entropy.py"             "hooks/redact/recognizers/entropy.py"
    "scripts/redact/recognizers/pii.py"                 "hooks/redact/recognizers/pii.py"
    "scripts/redact/recognizers/secrets.py"             "hooks/redact/recognizers/secrets.py"
    "scripts/redact/validators/__init__.py"             "hooks/redact/validators/__init__.py"
    "scripts/redact/validators/luhn.py"                 "hooks/redact/validators/luhn.py"

    # Slash commands — repo: .claude/commands/  live: commands/
    ".claude/commands/brain-search.md"       "commands/brain-search.md"
    ".claude/commands/brain-capture.md"      "commands/brain-capture.md"
    ".claude/commands/brain-recent.md"       "commands/brain-recent.md"
    ".claude/commands/brain-context.md"      "commands/brain-context.md"
    ".claude/commands/brain-forget.md"       "commands/brain-forget.md"
    ".claude/commands/brain-inspect.md"      "commands/brain-inspect.md"
    ".claude/commands/brain-promote.md"      "commands/brain-promote.md"
    ".claude/commands/brain-replay.md"       "commands/brain-replay.md"
    ".claude/commands/brain-stats.md"        "commands/brain-stats.md"
    ".claude/commands/brain-timeline.md"     "commands/brain-timeline.md"
    ".claude/commands/brain-trace.md"        "commands/brain-trace.md"
    ".claude/commands/bead-create.md"        "commands/bead-create.md"
    ".claude/commands/bead-link.md"          "commands/bead-link.md"
    ".claude/commands/bead-list.md"          "commands/bead-list.md"
    ".claude/commands/bead-ready.md"         "commands/bead-ready.md"
    ".claude/commands/bead-show.md"          "commands/bead-show.md"
    ".claude/commands/bead-update.md"        "commands/bead-update.md"
    ".claude/commands/context-check.md"      "commands/context-check.md"
    ".claude/commands/new-session.md"        "commands/new-session.md"
    ".claude/commands/prime-agent.md"        "commands/prime-agent.md"
    ".claude/commands/prime-project.md"      "commands/prime-project.md"
    ".claude/commands/quick-context.md"      "commands/quick-context.md"
    ".claude/commands/handoff.md"            "commands/handoff.md"
    ".claude/commands/summary.md"            "commands/summary.md"
    ".claude/commands/export-context.md"     "commands/export-context.md"
    ".claude/commands/load-context.md"       "commands/load-context.md"

    # Skills — repo: skills/  live: skills/
    "skills/excalidraw-diagram/SKILL.md"     "skills/excalidraw-diagram/SKILL.md"
    "skills/gensi-prompt-audit/SKILL.md"     "skills/gensi-prompt-audit/SKILL.md"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_sha256() { shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1; }

# Portable mtime in epoch seconds. GNU coreutils uses `stat -c %Y`; BSD/macOS
# uses `stat -f %m`. Detect once. The old inline `stat -f %m || stat -c %Y`
# chain was broken on Linux: there `-f` means --file-system, so stat printed the
# filesystem block for the valid file to stdout AND exited non-zero, the `||`
# then appended the fallback integer, and $() captured multi-line garbage. The
# numeric compare failed and every differing file fell through to REPO-NEWER —
# silently disabling LIVE-NEWER detection on Linux (CI).
if stat -c "%Y" . >/dev/null 2>&1; then
    _mtime() { stat -c "%Y" "$1"; }   # GNU / Linux
else
    _mtime() { stat -f "%m" "$1"; }   # BSD / macOS
fi

# ANSI colors (disabled when not a tty to avoid junk in CI logs)
if [ -t 1 ]; then
    C_RED="\033[0;31m"
    C_GREEN="\033[0;32m"
    C_YELLOW="\033[1;33m"
    C_CYAN="\033[0;36m"
    C_RESET="\033[0m"
else
    C_RED="" C_GREEN="" C_YELLOW="" C_CYAN="" C_RESET=""
fi

# ---------------------------------------------------------------------------
# Comparison pass
# Sets global DRIFT_COUNT after running.
# ---------------------------------------------------------------------------
DRIFT_COUNT=0

run_check() {
    local ok_count=0
    local repo_newer_count=0
    local live_newer_count=0
    local missing_live_count=0
    local missing_repo_count=0
    local total=0

    local -a statuses=()
    local -a repo_paths=()
    local -a live_paths=()

    local i=0
    while [ $((i + 1)) -lt ${#FILE_MAP[@]} ]; do
        local repo_rel="${FILE_MAP[$i]}"
        local live_rel="${FILE_MAP[$((i+1))]}"
        i=$((i + 2))
        total=$((total + 1))

        local repo_abs="$REPO_ROOT/$repo_rel"
        local live_abs="$LIVE_ROOT/$live_rel"

        local status
        local repo_exists=false live_exists=false
        [ -f "$repo_abs" ] && repo_exists=true
        [ -f "$live_abs" ] && live_exists=true

        if ! $repo_exists && ! $live_exists; then
            status="MISSING-BOTH"
        elif ! $repo_exists; then
            status="MISSING-IN-REPO"
            missing_repo_count=$((missing_repo_count + 1))
        elif ! $live_exists; then
            status="MISSING-IN-LIVE"
            missing_live_count=$((missing_live_count + 1))
        else
            local repo_sha live_sha
            repo_sha=$(_sha256 "$repo_abs")
            live_sha=$(_sha256 "$live_abs")
            if [ "$repo_sha" = "$live_sha" ]; then
                status="IDENTICAL"
                ok_count=$((ok_count + 1))
            else
                local repo_mtime live_mtime
                repo_mtime=$(_mtime "$repo_abs")
                live_mtime=$(_mtime "$live_abs")
                if [ "$live_mtime" -gt "$repo_mtime" ]; then
                    status="LIVE-NEWER"
                    live_newer_count=$((live_newer_count + 1))
                else
                    status="REPO-NEWER"
                    repo_newer_count=$((repo_newer_count + 1))
                fi
            fi
        fi

        statuses+=("$status")
        repo_paths+=("$repo_rel")
        live_paths+=("$live_rel")
    done

    # Print table — sorted so LIVE-NEWER rises to the top (print them first)
    printf "\n%-20s  %-52s  %s\n" "STATUS" "REPO PATH" "LIVE PATH"
    printf "%-20s  %-52s  %s\n" \
        "--------------------" \
        "----------------------------------------------------" \
        "----------------------------------------------------"

    # Two passes: LIVE-NEWER first (the dangerous case), then the rest
    for pass in "LIVE-NEWER" "REPO-NEWER" "MISSING-IN-LIVE" "MISSING-IN-REPO" "IDENTICAL" "MISSING-BOTH"; do
        for j in "${!statuses[@]}"; do
            [ "${statuses[$j]}" != "$pass" ] && continue
            local color="$C_RESET"
            case "$pass" in
                LIVE-NEWER)      color="$C_RED" ;;      # dangerous: silent staleness
                REPO-NEWER)      color="$C_CYAN" ;;
                MISSING-IN-LIVE) color="$C_YELLOW" ;;
                MISSING-IN-REPO) color="$C_YELLOW" ;;
                IDENTICAL)       color="$C_GREEN" ;;
                MISSING-BOTH)    color="$C_YELLOW" ;;
            esac
            printf "${color}%-20s${C_RESET}  %-52s  %s\n" \
                "${statuses[$j]}" "${repo_paths[$j]}" "${live_paths[$j]}"
        done
    done
    echo ""

    # Summary
    DRIFT_COUNT=$((live_newer_count + repo_newer_count + missing_live_count + missing_repo_count))
    echo "Summary: $ok_count identical"
    [ "$live_newer_count"   -gt 0 ] && printf "${C_RED}  LIVE-NEWER:      %d  <-- REPO IS STALE (dangerous)${C_RESET}\n" "$live_newer_count"
    [ "$repo_newer_count"   -gt 0 ] && printf "${C_CYAN}  REPO-NEWER:      %d${C_RESET}\n" "$repo_newer_count"
    [ "$missing_live_count" -gt 0 ] && printf "${C_YELLOW}  MISSING-IN-LIVE: %d${C_RESET}\n" "$missing_live_count"
    [ "$missing_repo_count" -gt 0 ] && printf "${C_YELLOW}  MISSING-IN-REPO: %d${C_RESET}\n" "$missing_repo_count"

    if [ "$DRIFT_COUNT" -eq 0 ]; then
        printf "${C_GREEN}All $ok_count files identical.${C_RESET}\n"
    else
        echo "Total drift: $DRIFT_COUNT file(s) (of $total tracked)"
    fi
}

# ---------------------------------------------------------------------------
# Pull-live pass: copy LIVE-NEWER files back into the repo
# ---------------------------------------------------------------------------
run_pull_live() {
    echo ""
    echo "--- pull-live: copying LIVE-NEWER files → repo ---"
    echo "    (Only files where live is newer AND content differs)"
    echo ""

    local copied=0
    local i=0
    while [ $((i + 1)) -lt ${#FILE_MAP[@]} ]; do
        local repo_rel="${FILE_MAP[$i]}"
        local live_rel="${FILE_MAP[$((i+1))]}"
        i=$((i + 2))

        local repo_abs="$REPO_ROOT/$repo_rel"
        local live_abs="$LIVE_ROOT/$live_rel"

        [ ! -f "$live_abs" ] && continue
        [ ! -f "$repo_abs" ] && continue

        local repo_sha live_sha
        repo_sha=$(_sha256 "$repo_abs")
        live_sha=$(_sha256 "$live_abs")
        [ "$repo_sha" = "$live_sha" ] && continue

        local repo_mtime live_mtime
        repo_mtime=$(_mtime "$repo_abs")
        live_mtime=$(_mtime "$live_abs")
        [ "$live_mtime" -le "$repo_mtime" ] && continue

        # Live is newer and content differs — copy into repo
        mkdir -p "$(dirname "$repo_abs")"
        cp "$live_abs" "$repo_abs"
        printf "${C_RED}  PULLED  %s  →  %s${C_RESET}\n" "$live_rel" "$repo_rel"
        copied=$((copied + 1))
    done

    if [ "$copied" -eq 0 ]; then
        echo "  nothing to pull (no LIVE-NEWER files with differing content)"
    else
        printf "${C_RED}  %d file(s) pulled from live into repo.${C_RESET}\n" "$copied"
        echo "  Review the changes and commit them to capture live evolution."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "install-check: repo vs live install"
echo "  repo root: $REPO_ROOT"
echo "  live root: $LIVE_ROOT"

run_check

if $PULL_LIVE; then
    run_pull_live
    exit $([ "$DRIFT_COUNT" -gt 0 ] && echo 1 || echo 0)
fi

if $STRICT && [ "$DRIFT_COUNT" -gt 0 ]; then
    exit 1
fi

exit 0
