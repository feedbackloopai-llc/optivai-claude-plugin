#!/bin/bash
# ABOUTME: Quick skill/command sync from plugin repo to global ~/.claude/
# ABOUTME: Run after git pull or manually to update skills

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CLAUDE_DIR="${HOME}/.claude"
COMMANDS_DIR="${CLAUDE_DIR}/commands"
AGENTS_DIR="${CLAUDE_DIR}/agents"
LIB_DIR="${CLAUDE_DIR}/lib"
SUPERPOWERS_DIR="${CLAUDE_DIR}/commands/superpowers"

# Create directories if needed
mkdir -p "$COMMANDS_DIR" "$AGENTS_DIR" "$LIB_DIR" "$SUPERPOWERS_DIR"

# Sync commands/skills
echo "Syncing skills to ~/.claude/commands/..."
cp "$REPO_DIR/.claude/commands/"*.md "$COMMANDS_DIR/" 2>/dev/null
cp "$REPO_DIR/.claude/commands/"*.py "$COMMANDS_DIR/" 2>/dev/null
cp "$REPO_DIR/.claude/commands/"*.json "$COMMANDS_DIR/" 2>/dev/null

# Sync superpowers skills
if [ -d "$REPO_DIR/.claude/commands/superpowers" ]; then
    echo "Syncing superpowers skills..."
    cp "$REPO_DIR/.claude/commands/superpowers/"*.md "$SUPERPOWERS_DIR/" 2>/dev/null
fi

# Sync agents
echo "Syncing agents to ~/.claude/agents/..."
cp "$REPO_DIR/agents/"*.md "$AGENTS_DIR/" 2>/dev/null

# Sync lib (client libraries)
if [ -d "$REPO_DIR/claude_lib" ]; then
    echo "Syncing client libraries to ~/.claude/lib/..."
    cp "$REPO_DIR/claude_lib/"*.py "$LIB_DIR/" 2>/dev/null
fi

# Count what was synced
CMD_COUNT=$(ls -1 "$REPO_DIR/.claude/commands/"*.md 2>/dev/null | wc -l | tr -d ' ')
AGENT_COUNT=$(ls -1 "$REPO_DIR/agents/"*.md 2>/dev/null | wc -l | tr -d ' ')

echo ""
echo "✓ Synced $CMD_COUNT skills and $AGENT_COUNT agents"
echo "  Skills available immediately (hot reload)"
