#!/bin/bash
# install.sh - Cross-Platform Installation for OptivAI Claude Plugin
#
# This script detects the operating system and runs the appropriate installer:
# - Windows (Git Bash/MSYS2/Cygwin): Launches PowerShell installer
# - macOS: Runs native bash installer with launchd
# - Linux: Runs native bash installer with systemd/cron
#
# Components installed:
#   1. Hook scripts (logging)
#   2. Slash commands
#   3. Agent templates
#   4. Skills
#   5. Configuration file
#   6. PostgreSQL sync daemon
#
# Usage:
#   ./install.sh [options]
#
# Options:
#   --skip-daemon    Skip background daemon installation
#   --force          Overwrite existing installation
#   --uninstall      Remove the installation (keeps logs and user data)
#   --help           Show this help message

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CLAUDE_DIR="${HOME}/.claude"
HOOKS_DIR="${CLAUDE_DIR}/hooks"
COMMANDS_DIR="${CLAUDE_DIR}/commands"
AGENTS_DIR="${CLAUDE_DIR}/agents"
SKILLS_DIR="${CLAUDE_DIR}/skills"
LOGS_DIR="${CLAUDE_DIR}/logs"
MEMORY_DIR="${CLAUDE_DIR}/optivai-memory"
SETTINGS_FILE="${CLAUDE_DIR}/settings.json"
CONFIG_FILE="${HOOKS_DIR}/auto-logger-config.json"

# Configuration
PLUGIN_NAME="OptivAI Claude Plugin"
LAUNCHD_LABEL="com.feedbackloopai.claude-pg-sync"
SYSTEMD_SERVICE="claude-pg-sync"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse arguments
SKIP_DAEMON=false
FORCE=false
UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-daemon)
            SKIP_DAEMON=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --skip-daemon    Skip background daemon installation"
            echo "  --force          Overwrite existing installation"
            echo "  --uninstall      Remove the installation (keeps logs and user data)"
            echo "  --help           Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Helper functions
print_header() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }
print_status() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[X]${NC} $1"; }
print_info() { echo -e "${CYAN}[*]${NC} $1"; }

# Prompt with default value
prompt_with_default() {
    local prompt="$1"
    local default="$2"
    local result

    if [ -n "$default" ]; then
        read -p "$prompt [$default]: " result
        echo "${result:-$default}"
    else
        read -p "$prompt: " result
        echo "$result"
    fi
}

# Prompt for required value (loops until provided)
prompt_required() {
    local prompt="$1"
    local result=""

    while [ -z "$result" ]; do
        read -p "$prompt [required]: " result
        if [ -z "$result" ]; then
            print_error "This field is required"
        fi
    done
    echo "$result"
}

# Interactive PostgreSQL configuration
configure_postgresql() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Neon PostgreSQL Configuration${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo "Enter your Neon PostgreSQL connection string."
    echo "Get it from: https://console.neon.tech → your project → Connection Details"
    echo "Use the DIRECT endpoint (not pooler). See docs/NEON_SETUP_GUIDE.md for details."
    echo ""

    # Check if DATABASE_URL is already set
    if [ -n "$DATABASE_URL" ]; then
        print_status "Found DATABASE_URL in environment"
        read -p "Use existing DATABASE_URL? (Y/n): " USE_EXISTING
        if [ "$USE_EXISTING" != "n" ] && [ "$USE_EXISTING" != "N" ]; then
            PG_CONNECTION_STRING="$DATABASE_URL"
        else
            PG_CONNECTION_STRING=$(prompt_required "PostgreSQL connection string (postgresql://user:pass@host/db?sslmode=require)")
        fi
    else
        PG_CONNECTION_STRING=$(prompt_required "PostgreSQL connection string (postgresql://user:pass@host/db?sslmode=require)")
    fi

    # Validate connection string format
    if [[ "$PG_CONNECTION_STRING" != postgresql://* ]]; then
        print_warning "Connection string should start with postgresql://"
    fi

    # Warn about pooler endpoint
    if [[ "$PG_CONNECTION_STRING" == *"-pooler"* ]]; then
        print_warning "Detected pooler endpoint (-pooler in hostname)"
        print_warning "pgvector requires the DIRECT endpoint. Remove '-pooler' from the hostname."
    fi

    # Write config file
    cat > "$CONFIG_FILE" << PG_CONFIG
{
  "enabled": true,
  "log_tool_operations": true,
  "log_user_prompts": true,
  "log_level": "info",
  "max_prompt_length": 500,
  "session_tracking": true,
  "log_directory_mode": "global",
  "global_log_path": "~/.claude/logs",
  "project_log_subdir": ".claude/logs",
  "destinations": {
    "local": {
      "enabled": true
    },
    "postgresql": {
      "enabled": true,
      "connection_string": "$PG_CONNECTION_STRING",
      "sync": {
        "batch_size": 100,
        "flush_interval_seconds": 60,
        "retry_attempts": 3,
        "retry_delay_seconds": 5
      }
    }
  }
}
PG_CONFIG

    echo ""
    print_status "PostgreSQL configuration saved to: $CONFIG_FILE"

    # Suggest adding to shell profile
    echo ""
    print_info "Tip: Add DATABASE_URL to your shell profile for scripts that use it directly:"
    echo "  echo 'export DATABASE_URL=\"$PG_CONNECTION_STRING\"' >> ~/.zshrc"
}

# Install Ollama for local LLM fallback
install_ollama() {
    print_header "Installing Ollama (Local LLM Fallback)"

    if command -v ollama &> /dev/null; then
        print_status "Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown version')"
    else
        print_info "Installing Ollama..."
        if curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null; then
            print_status "Ollama installed"
        else
            print_warning "Ollama install failed — metadata extraction will use Claude API only"
            return
        fi
    fi

    # Start Ollama if not running
    if ! curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        print_info "Starting Ollama..."
        ollama serve > /dev/null 2>&1 &
        sleep 3
    fi

    # Pull the model
    if ollama list 2>/dev/null | grep -q "llama3.1"; then
        print_status "llama3.1 model already available"
    else
        print_info "Pulling llama3.1 model (~4.7GB, this may take a few minutes)..."
        if ollama pull llama3.1:latest 2>/dev/null; then
            print_status "llama3.1 model ready"
        else
            print_warning "Model pull failed — will retry automatically on first use"
        fi
    fi
}

# Detect operating system
detect_os() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            echo "windows"
            ;;
        Darwin*)
            echo "macos"
            ;;
        Linux*)
            echo "linux"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# Windows installer (launches PowerShell)
install_windows() {
    print_info "Detected Windows environment (Git Bash/MSYS2/Cygwin)"
    print_info "Launching PowerShell installer..."
    echo ""

    PS_SCRIPT="$SCRIPT_DIR/install.ps1"

    # Convert to Windows path if needed
    if command -v cygpath &> /dev/null; then
        PS_SCRIPT=$(cygpath -w "$PS_SCRIPT")
    fi

    # Build PowerShell arguments
    PS_ARGS=()
    if [ "$SKIP_DAEMON" = true ]; then
        PS_ARGS+=("-SkipDaemon")
    fi
    if [ "$FORCE" = true ]; then
        PS_ARGS+=("-Force")
    fi
    if [ "$UNINSTALL" = true ]; then
        PS_ARGS+=("-Uninstall")
    fi

    # Check if PowerShell is available
    if command -v powershell.exe &> /dev/null; then
        powershell.exe -ExecutionPolicy Bypass -File "$PS_SCRIPT" "${PS_ARGS[@]}"
    elif command -v pwsh.exe &> /dev/null; then
        pwsh.exe -ExecutionPolicy Bypass -File "$PS_SCRIPT" "${PS_ARGS[@]}"
    elif command -v pwsh &> /dev/null; then
        pwsh -ExecutionPolicy Bypass -File "$PS_SCRIPT" "${PS_ARGS[@]}"
    else
        print_error "PowerShell not found!"
        print_warning "Please run the installer directly:"
        echo "  powershell -ExecutionPolicy Bypass -File \"$PS_SCRIPT\""
        exit 1
    fi
}

# Uninstall for macOS/Linux (Option B: remove plugin files, keep logs/user data)
uninstall_unix() {
    local OS_TYPE="$1"

    echo ""
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}  $PLUGIN_NAME Uninstaller${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo ""

    print_info "Uninstalling $PLUGIN_NAME..."
    print_info "Note: Logs and user data will be preserved"
    echo ""

    # Remove daemon
    if [ "$OS_TYPE" = "macos" ]; then
        PLIST_FILE="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
        if [ -f "$PLIST_FILE" ]; then
            launchctl unload "$PLIST_FILE" 2>/dev/null || true
            rm -f "$PLIST_FILE"
            print_status "Removed launchd daemon: $LAUNCHD_LABEL"
        else
            print_warning "Launchd daemon not found (already removed)"
        fi
    else
        # Linux: Remove systemd service
        if systemctl --user is-enabled "$SYSTEMD_SERVICE" 2>/dev/null; then
            systemctl --user stop "$SYSTEMD_SERVICE" 2>/dev/null || true
            systemctl --user disable "$SYSTEMD_SERVICE" 2>/dev/null || true
            rm -f "$HOME/.config/systemd/user/${SYSTEMD_SERVICE}.service"
            systemctl --user daemon-reload
            print_status "Removed systemd service: $SYSTEMD_SERVICE"
        fi

        # Remove cron job if exists
        if crontab -l 2>/dev/null | grep -q "pg_sync.py"; then
            crontab -l 2>/dev/null | grep -v "pg_sync.py" | crontab - 2>/dev/null || true
            print_status "Removed cron job"
        fi
    fi

    # Remove hook scripts (but keep config)
    if [ -d "$HOOKS_DIR" ]; then
        print_info "Removing hook scripts..."
        local hook_files=(
            "log_writer.py"
            "pre-tool-use.py"
            "user-prompt-submit.py"
            "pg_sync.py"
            "memory_writer.py"
            "subagent_context.py"
            "context_primer.py"
            "beads_writer.py"
            "redact_secrets.py"
            "session_summary.py"
            "stop-hook.sh"
            "stop-hook.py"
            "brain_hook.py"
            "till_done.py"
            "open_brain.py"
        )

        for file in "${hook_files[@]}"; do
            rm -f "$HOOKS_DIR/$file"
        done

        # Remove __pycache__
        rm -rf "$HOOKS_DIR/__pycache__"

        print_status "Removed hook scripts"
        print_warning "Kept auto-logger-config.json (contains your settings)"
    fi

    # Remove commands
    if [ -d "$COMMANDS_DIR" ]; then
        print_info "Removing commands..."
        local count=$(find "$COMMANDS_DIR" -type f | wc -l | tr -d ' ')
        rm -rf "$COMMANDS_DIR"/*
        print_status "Removed $count command files"
    fi

    # Remove agents
    if [ -d "$AGENTS_DIR" ]; then
        print_info "Removing agent templates..."
        local count=$(find "$AGENTS_DIR" -name "*.md" -type f | wc -l | tr -d ' ')
        rm -f "$AGENTS_DIR"/*.md
        print_status "Removed $count agent templates"
    fi

    # Remove skills
    if [ -d "$SKILLS_DIR" ]; then
        print_info "Removing skills..."
        local count=$(find "$SKILLS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
        if [ "$count" -gt 0 ]; then
            rm -rf "$SKILLS_DIR"/*
            print_status "Removed $count skill files"
        fi
    fi

    # Remove settings.json (backup first)
    if [ -f "$SETTINGS_FILE" ]; then
        local backup_file="${SETTINGS_FILE}.uninstall-backup.$(date +%Y%m%d-%H%M%S)"
        cp "$SETTINGS_FILE" "$backup_file"
        rm -f "$SETTINGS_FILE"
        print_status "Removed settings.json (backed up to $backup_file)"
    fi

    echo ""
    echo -e "${GREEN}========================================${NC}"
    print_status "Uninstall complete!"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Preserved directories:"
    echo "  - $LOGS_DIR (activity logs)"
    echo "  - $MEMORY_DIR (memory system data)"
    echo "  - $CONFIG_FILE (your configuration)"
    echo ""
    echo "To completely remove all data, manually delete:"
    echo "  $CLAUDE_DIR"
    echo ""
}

# macOS/Linux installer
install_unix() {
    local OS_TYPE="$1"

    if [ "$UNINSTALL" = true ]; then
        uninstall_unix "$OS_TYPE"
        return
    fi

    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║   $PLUGIN_NAME - Installation        ║"
    echo "║   Platform: $OS_TYPE                                            ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    # Check prerequisites
    print_header "Checking Prerequisites"

    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is required but not installed"
        exit 1
    fi
    print_status "Python 3 found: $(python3 --version)"

    if ! python3 -c "import psycopg2" 2>/dev/null; then
        print_warning "psycopg2-binary not installed — installing..."
        python3 -m pip install psycopg2-binary pgvector sentence-transformers anthropic --break-system-packages 2>/dev/null \
            || python3 -m pip install psycopg2-binary pgvector sentence-transformers anthropic 2>/dev/null \
            || print_warning "Auto-install failed. Run: pip install psycopg2-binary pgvector sentence-transformers anthropic"
    else
        print_status "PostgreSQL connector installed"
    fi

    # Create directories
    print_header "Creating Directories"

    mkdir -p "$HOOKS_DIR"
    mkdir -p "$COMMANDS_DIR"
    mkdir -p "$AGENTS_DIR"
    mkdir -p "$SKILLS_DIR"
    mkdir -p "$LOGS_DIR"
    mkdir -p "$MEMORY_DIR"

    print_status "Created ~/.claude directory structure"

    # Install hook scripts
    print_header "Installing Hook Scripts"

    local hook_files=(
        "log_writer.py"
        "pre-tool-use.py"
        "user-prompt-submit.py"
        "pg_sync.py"
        "memory_writer.py"
        "subagent_context.py"
        "context_primer.py"
        "beads_writer.py"
        "redact_secrets.py"
        "session_summary.py"
        "stop-hook.sh"
        "stop-hook.py"
        "brain_hook.py"
        "till_done.py"
    )

    local REPO_HOOKS_DIR="$REPO_DIR/scripts/hooks"
    local count=0
    for file in "${hook_files[@]}"; do
        if [ -f "$REPO_HOOKS_DIR/$file" ]; then
            cp "$REPO_HOOKS_DIR/$file" "$HOOKS_DIR/"
            count=$((count + 1))
        fi
    done
    chmod +x "$HOOKS_DIR/stop-hook.sh" 2>/dev/null || true

    # Deploy open_brain.py from scripts/ (not hooks/)
    if [ -f "$REPO_DIR/scripts/open_brain.py" ]; then
        cp "$REPO_DIR/scripts/open_brain.py" "$HOOKS_DIR/"
        count=$((count + 1))
    fi

    # Deploy BRAIN_SCHEMA_PG.sql so --init works post-install
    mkdir -p "$HOME/.claude/sql"
    cp "$REPO_DIR/sql/BRAIN_SCHEMA_PG.sql" "$HOME/.claude/sql/" 2>/dev/null || true
    cp "$REPO_DIR/sql/VW_CLAUDE_CODE_VIEWS_PG.sql" "$HOME/.claude/sql/" 2>/dev/null || true

    print_status "Installed $count hook scripts to $HOOKS_DIR"
    print_status "  - pre-tool-use.py (logging + auto-beads)"
    print_status "  - session_summary.py (token counting per session)"
    print_status "  - beads_writer.py (Beads hook integration)"
    print_status "  - redact_secrets.py (API key/token filtering)"

    # Install slash commands
    print_header "Installing Slash Commands"

    # Copy commands including subdirectories (e.g., superpowers/)
    if [ -d "$REPO_DIR/.claude/commands" ]; then
        cp -r "$REPO_DIR/.claude/commands/"* "$COMMANDS_DIR/" 2>/dev/null || true
    fi

    COMMAND_COUNT=$(ls -1 "$COMMANDS_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
    print_status "Installed $COMMAND_COUNT slash commands to $COMMANDS_DIR"

    # Install agent templates
    print_header "Installing Agent Templates"

    cp "$REPO_DIR/agents/"*.md "$AGENTS_DIR/" 2>/dev/null || true

    AGENT_COUNT=$(ls -1 "$AGENTS_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
    print_status "Installed $AGENT_COUNT agent templates to $AGENTS_DIR"

    # Install skills
    print_header "Installing Skills"

    if [ -d "$REPO_DIR/skills" ]; then
        cp "$REPO_DIR/skills/"* "$SKILLS_DIR/" 2>/dev/null || true
        SKILL_COUNT=$(ls -1 "$SKILLS_DIR" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$SKILL_COUNT" -gt 0 ]; then
            print_status "Installed $SKILL_COUNT skill files to $SKILLS_DIR"
        fi
    fi

    # Install Beads CLI
    print_header "Installing Beads CLI"

    if [ -f "$REPO_DIR/setup.py" ] && [ -d "$REPO_DIR/scripts/beads" ]; then
        # Install in development mode - try multiple approaches for compatibility
        local INSTALL_SUCCESS=false

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

        # Copy bead commands to global commands
        cp "$REPO_DIR/.claude/commands/bead-"*.md "$COMMANDS_DIR/" 2>/dev/null || true
        cp "$REPO_DIR/.claude/commands/mol-"*.md "$COMMANDS_DIR/" 2>/dev/null || true

        BEAD_CMD_COUNT=$(ls -1 "$COMMANDS_DIR"/bead-*.md "$COMMANDS_DIR"/mol-*.md 2>/dev/null | wc -l | tr -d ' ')
        print_status "Installed $BEAD_CMD_COUNT bead commands"
    else
        print_warning "Beads not found in repository"
    fi

    # Create beads directory
    mkdir -p "${CLAUDE_DIR}/beads"
    print_status "Created ~/.claude/beads/ (global beads storage)"

    # Install Ollama (local LLM for metadata extraction fallback)
    install_ollama

    # Install configuration
    print_header "Installing Configuration"

    if [ -f "$CONFIG_FILE" ]; then
        print_warning "Configuration file already exists at $CONFIG_FILE"
        read -p "Do you want to reconfigure PostgreSQL settings? (y/N): " RECONFIGURE
        if [ "$RECONFIGURE" = "y" ] || [ "$RECONFIGURE" = "Y" ]; then
            configure_postgresql
        else
            print_status "Keeping existing configuration"
        fi
    else
        configure_postgresql
    fi

    # Generate settings.json
    print_header "Configuring Claude Code Settings"

    if [ -f "$SETTINGS_FILE" ] && [ "$FORCE" = false ]; then
        print_warning "settings.json already exists"
        BACKUP_FILE="${SETTINGS_FILE}.backup.$(date +%Y%m%d-%H%M%S)"
        cp "$SETTINGS_FILE" "$BACKUP_FILE"
        print_status "Backed up to: $BACKUP_FILE"
    fi

    # Prompt for user email (for token usage attribution)
    print_header "User Identity Configuration"
    print_info "Your email is used for token usage reporting and developer attribution."
    USER_EMAIL=$(prompt_with_default "Your company email" "")
    ORG_NAME=$(prompt_with_default "Organization name" "FeedbackLoopAI")

    # Build env block
    local ENV_BLOCK=""
    if [ -n "$USER_EMAIL" ] || [ -n "$ORG_NAME" ]; then
        ENV_BLOCK=$(cat << ENVBLOCK
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_USER_EMAIL": "$USER_EMAIL",
    "CLAUDE_ORG_NAME": "$ORG_NAME"
  },
ENVBLOCK
)
    else
        ENV_BLOCK=$(cat << ENVBLOCK
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
ENVBLOCK
)
    fi

    cat > "$SETTINGS_FILE" << SETTINGS
{
$ENV_BLOCK
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/pre-tool-use.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/user-prompt-submit.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/session_summary.py"
          },
          {
            "type": "command",
            "command": "bash \"\$HOME/.claude/hooks/stop-hook.sh\""
          }
        ]
      }
    ]
  }
}
SETTINGS

    print_status "Generated settings.json"

    # Install PostgreSQL sync daemon
    if [ "$SKIP_DAEMON" = false ]; then
        print_header "Installing PostgreSQL Sync Daemon"

        if [ "$OS_TYPE" = "macos" ]; then
            if [ -f "$REPO_DIR/scripts/install-launchd.sh" ]; then
                bash "$REPO_DIR/scripts/install-launchd.sh"
            else
                print_warning "install-launchd.sh not found, skipping daemon setup"
            fi
        else
            # Linux: Use systemd or cron
            install_linux_daemon
        fi
    else
        print_info "Skipping daemon installation (--skip-daemon specified)"
    fi

    # Installation complete
    print_header "Installation Complete"

    echo "Next steps:"
    echo "  1. Ensure your PostgreSQL credentials is at the configured path"
    echo "  2. Restart Claude Code"
    echo ""
    echo "Verify installation:"
    if [ "$OS_TYPE" = "macos" ]; then
        echo "  • Check daemon: launchctl list | grep claude-pg"
    else
        echo "  • Check daemon: systemctl --user status $SYSTEMD_SERVICE"
    fi
    echo "  • Check logs: ls ~/.claude/logs/"
    echo "  • Test sync: python3 ~/.claude/hooks/pg_sync.py --status"
    echo ""
    print_status "Installation complete!"
}

install_linux_daemon() {
    local SYNC_SCRIPT="$HOOKS_DIR/pg_sync.py"
    local LOG_FILE="$LOGS_DIR/sync_daemon.log"

    # Check if systemd user services are available
    if [ -d "$HOME/.config/systemd/user" ] || mkdir -p "$HOME/.config/systemd/user" 2>/dev/null; then
        local SERVICE_FILE="$HOME/.config/systemd/user/${SYSTEMD_SERVICE}.service"

        cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Claude Code PostgreSQL Sync Daemon (OptivAI Plugin)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $SYNC_SCRIPT --log-file $LOG_FILE
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
EOF

        # Enable and start the service
        systemctl --user daemon-reload
        systemctl --user enable "$SYSTEMD_SERVICE"
        systemctl --user start "$SYSTEMD_SERVICE"

        print_status "Installed systemd user service: $SYSTEMD_SERVICE"
        echo "  Check status: systemctl --user status $SYSTEMD_SERVICE"
    else
        # Fall back to cron
        print_warning "systemd user services not available, using cron instead"
        local CRON_CMD="* * * * * /usr/bin/python3 $SYNC_SCRIPT --once >> $LOG_FILE 2>&1"

        # Add to crontab if not already present
        (crontab -l 2>/dev/null | grep -v "pg_sync.py"; echo "$CRON_CMD") | crontab -

        print_status "Installed cron job for sync daemon"
        echo "  Check with: crontab -l"
    fi
}

# Main execution
OS=$(detect_os)

case "$OS" in
    windows)
        install_windows
        ;;
    macos|linux)
        install_unix "$OS"
        ;;
    *)
        print_error "Unknown operating system: $(uname -s)"
        exit 1
        ;;
esac
