#!/bin/bash
# upgrade.sh - Upgrade existing installation to include Beads
#
# For existing users who have the plugin installed and want to add Beads.
# This is a lighter-weight update than running full install.sh.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CLAUDE_DIR="${HOME}/.claude"
COMMANDS_DIR="${CLAUDE_DIR}/commands"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }
print_status() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Beads Upgrade - Add Graph-Based Knowledge System      ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Create beads directories
print_header "Creating Beads Directories"

mkdir -p "${CLAUDE_DIR}/beads"
print_status "Created ~/.claude/beads/ (global beads storage)"

# Install/update Beads CLI
print_header "Installing Beads CLI"

if [ -f "$REPO_DIR/setup.py" ] && [ -d "$REPO_DIR/scripts/beads" ]; then
    # Install in development mode - try multiple approaches for compatibility
    INSTALL_SUCCESS=false

    # Method 1: Standard pip via python3
    if ! $INSTALL_SUCCESS; then
        python3 -m pip install -e "$REPO_DIR" 2>/dev/null && INSTALL_SUCCESS=true
    fi

    # Method 2: With --break-system-packages (needed on newer macOS/Homebrew)
    if ! $INSTALL_SUCCESS; then
        python3 -m pip install -e "$REPO_DIR" --break-system-packages 2>/dev/null && INSTALL_SUCCESS=true
    fi

    # Method 3: User install as fallback
    if ! $INSTALL_SUCCESS; then
        python3 -m pip install -e "$REPO_DIR" --user 2>/dev/null && INSTALL_SUCCESS=true
    fi

    if $INSTALL_SUCCESS; then
        print_status "Installed Beads CLI"
    else
        print_error "Failed to install Beads CLI"
        echo "    Try manually: python3 -m pip install -e $REPO_DIR --break-system-packages"
    fi
else
    print_error "Beads source not found in $REPO_DIR"
    exit 1
fi

# Copy bead commands
print_header "Installing Bead Commands"

if [ -d "$COMMANDS_DIR" ]; then
    cp "$REPO_DIR/.claude/commands/bead-"*.md "$COMMANDS_DIR/" 2>/dev/null || true
    cp "$REPO_DIR/.claude/commands/mol-"*.md "$COMMANDS_DIR/" 2>/dev/null || true

    BEAD_CMD_COUNT=$(ls -1 "$COMMANDS_DIR"/bead-*.md "$COMMANDS_DIR"/mol-*.md 2>/dev/null | wc -l | tr -d ' ')
    print_status "Installed $BEAD_CMD_COUNT bead/molecule commands"
else
    print_warning "Commands directory not found: $COMMANDS_DIR"
    echo "    Run full install.sh first, then upgrade.sh"
fi

# Update hook scripts (includes beads_writer integration)
print_header "Updating Hook Scripts"

HOOKS_DIR="${CLAUDE_DIR}/hooks"
REPO_HOOKS_DIR="$REPO_DIR/scripts/hooks"

if [ -d "$REPO_HOOKS_DIR" ] && [ -d "$HOOKS_DIR" ]; then
    # Core hooks with beads integration
    cp "$REPO_HOOKS_DIR/pre-tool-use.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/user-prompt-submit.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/beads_writer.py" "$HOOKS_DIR/"

    # Supporting modules
    cp "$REPO_HOOKS_DIR/memory_writer.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/log_writer.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/redact_secrets.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/subagent_context.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/context_primer.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/pg_sync.py" "$HOOKS_DIR/"
    cp "$REPO_HOOKS_DIR/stop-hook.sh" "$HOOKS_DIR/"
    chmod +x "$HOOKS_DIR/stop-hook.sh"

    print_status "Updated hook scripts with beads_writer integration"
    print_status "  - beads_writer.py (auto-creates beads for significant operations)"
    print_status "  - redact_secrets.py (API key/token filtering)"
    print_status "  - pre-tool-use.py, user-prompt-submit.py (updated)"
elif [ ! -d "$HOOKS_DIR" ]; then
    print_warning "Hooks directory not found: $HOOKS_DIR"
    echo "    Run full install.sh to set up hooks"
else
    print_warning "Repo hooks not found: $REPO_HOOKS_DIR"
fi

# Update settings.json hook paths if needed
print_header "Updating Settings"

SETTINGS_FILE="${CLAUDE_DIR}/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
    # Fix underscore → hyphen hook filenames
    if grep -q "pre_tool_use.py\|user_prompt_submit.py" "$SETTINGS_FILE" 2>/dev/null; then
        sed -i.bak \
            -e 's/pre_tool_use\.py/pre-tool-use.py/g' \
            -e 's/user_prompt_submit\.py/user-prompt-submit.py/g' \
            "$SETTINGS_FILE"
        rm -f "${SETTINGS_FILE}.bak"
        print_status "Fixed hook filenames in settings.json (underscore -> hyphen)"
    else
        print_status "settings.json hook paths already correct"
    fi
else
    print_warning "No settings.json found - run full install.sh to create"
fi

# Check for existing memory data
print_header "Checking for Migration Candidates"

MEMORY_DIR="${CLAUDE_DIR}/gz-observability-memory"
if [ -f "$MEMORY_DIR/work_log.yaml" ]; then
    WORK_LOG_COUNT=$(grep -c "timestamp:" "$MEMORY_DIR/work_log.yaml" 2>/dev/null || echo "0")
    print_status "Found work_log.yaml with ~$WORK_LOG_COUNT entries"
fi

if [ -f "$MEMORY_DIR/planned_tasks.yaml" ]; then
    print_status "Found planned_tasks.yaml"
fi

if [ -f "$MEMORY_DIR/handoff_context.yaml" ]; then
    print_status "Found handoff_context.yaml"
fi

CONTEXT_COUNT=$(ls -1 "$MEMORY_DIR"/*_context.yaml "$MEMORY_DIR"/*_project.yaml 2>/dev/null | wc -l | tr -d ' ' || echo "0")
if [ -n "$CONTEXT_COUNT" ] && [ "$CONTEXT_COUNT" -gt 0 ]; then
    print_status "Found $CONTEXT_COUNT project context files"
fi

# Migration instructions
print_header "Migration Instructions"

echo "To migrate your existing memory data to Beads:"
echo ""
echo "  1. Preview migration:"
echo "     ${GREEN}beads migrate --dry-run${NC}"
echo ""
echo "  2. Run migration:"
echo "     ${GREEN}beads migrate${NC}"
echo ""
echo "  3. Verify migrated beads (use --global flag):"
echo "     ${GREEN}beads list -g --label migrated${NC}"
echo ""
echo "Migration is idempotent - safe to run multiple times."
echo "Migrated beads go to global database (~/.claude/beads/)."
echo "Old YAML files remain in place for reference."

# Quick start
print_header "Beads Quick Start"

echo "Basic commands:"
echo "  ${GREEN}beads init${NC}         Initialize beads in current project"
echo "  ${GREEN}beads create \"Task\"${NC}  Create a new bead"
echo "  ${GREEN}beads list${NC}          List all beads"
echo "  ${GREEN}beads ready${NC}         Show beads ready to work"
echo ""
echo "Slash commands in Claude Code:"
echo "  ${GREEN}/bead-create${NC}          Create a new bead"
echo "  ${GREEN}/bead-list${NC}            List beads"
echo "  ${GREEN}/bead-ready${NC}           Show ready beads"
echo "  ${GREEN}/mol-pour${NC}             Instantiate a workflow molecule"

echo ""
print_header "Upgrade Complete"
print_status "Beads is now available!"
echo ""
echo "For documentation, see: $REPO_DIR/docs/MIGRATION_GUIDE.md"
echo ""
