#!/bin/bash
# install-launchd.sh
# Installs the Claude PostgreSQL Sync daemon as a macOS Launch Agent
#
# Usage: ./install-launchd.sh [--uninstall]

set -e

PLIST_NAME="com.feedbackloopai.claude-pg-sync.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SOURCE="${SCRIPT_DIR}/${PLIST_NAME}"
PLIST_DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
HOOKS_DIR="${HOME}/.claude/hooks"
LOGS_DIR="${HOME}/.claude/logs"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

uninstall() {
    echo "Uninstalling Claude PostgreSQL Sync daemon..."

    # Stop the daemon if running
    if launchctl list | grep -q "com.feedbackloopai.claude-pg-sync"; then
        launchctl unload "${PLIST_DEST}" 2>/dev/null || true
        print_status "Daemon stopped"
    fi

    # Remove the plist
    if [ -f "${PLIST_DEST}" ]; then
        rm "${PLIST_DEST}"
        print_status "Removed ${PLIST_DEST}"
    else
        print_warning "Plist not found at ${PLIST_DEST}"
    fi

    echo ""
    print_status "Uninstall complete"
    exit 0
}

install() {
    echo "Installing Claude PostgreSQL Sync daemon..."
    echo ""

    # Check prerequisites
    echo "Checking prerequisites..."

    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 not found. Please install Python 3.8+"
        exit 1
    fi
    print_status "Python 3 found: $(python3 --version)"

    # Check for source plist
    if [ ! -f "${PLIST_SOURCE}" ]; then
        print_error "Plist file not found: ${PLIST_SOURCE}"
        exit 1
    fi
    print_status "Source plist found"

    # Check for sync script
    if [ ! -f "${SCRIPT_DIR}/pg_sync.py" ]; then
        print_error "pg_sync.py not found in ${SCRIPT_DIR}"
        exit 1
    fi
    print_status "Sync script found"

    # Create required directories
    echo ""
    echo "Creating directories..."
    mkdir -p "${HOME}/Library/LaunchAgents"
    mkdir -p "${HOOKS_DIR}"
    mkdir -p "${LOGS_DIR}"
    print_status "Directories created"

    # Copy sync script to hooks directory if not already there
    if [ ! -f "${HOOKS_DIR}/pg_sync.py" ]; then
        cp "${SCRIPT_DIR}/pg_sync.py" "${HOOKS_DIR}/"
        print_status "Copied pg_sync.py to ${HOOKS_DIR}"
    else
        print_warning "pg_sync.py already exists in ${HOOKS_DIR}"
    fi

    # Stop existing daemon if running
    if launchctl list | grep -q "com.feedbackloopai.claude-pg-sync"; then
        print_warning "Stopping existing daemon..."
        launchctl unload "${PLIST_DEST}" 2>/dev/null || true
    fi

    # Create modified plist with expanded paths
    echo ""
    echo "Installing launch agent..."
    # Expand both ~ and $HOME placeholders
    sed -e "s|~/.claude|${HOME}/.claude|g" \
        -e "s|\\\$HOME|${HOME}|g" \
        "${PLIST_SOURCE}" > "${PLIST_DEST}"
    print_status "Installed plist to ${PLIST_DEST}"

    # Validate plist
    if ! plutil -lint "${PLIST_DEST}" > /dev/null 2>&1; then
        print_error "Plist validation failed"
        plutil -lint "${PLIST_DEST}"
        exit 1
    fi
    print_status "Plist validation passed"

    # Load the daemon
    echo ""
    echo "Starting daemon..."
    launchctl load "${PLIST_DEST}"

    # Verify it's running
    sleep 2
    if launchctl list | grep -q "com.feedbackloopai.claude-pg-sync"; then
        print_status "Daemon loaded successfully"
    else
        print_warning "Daemon may not have started. Check logs at ${LOGS_DIR}/sync_daemon_stderr.log"
    fi

    echo ""
    echo "============================================"
    print_status "Installation complete!"
    echo "============================================"
    echo ""
    echo "The sync daemon will:"
    echo "  • Start automatically at login"
    echo "  • Sync Claude Code activity to PostgreSQL every 60 seconds"
    echo "  • Restart automatically if it crashes"
    echo ""
    echo "Commands:"
    echo "  Status:  launchctl list | grep claude-pg"
    echo "  Stop:    launchctl unload ${PLIST_DEST}"
    echo "  Start:   launchctl load ${PLIST_DEST}"
    echo "  Logs:    tail -f ${LOGS_DIR}/sync_daemon_stderr.log"
    echo ""
    echo "To uninstall:"
    echo "  ${SCRIPT_DIR}/install-launchd.sh --uninstall"
    echo ""
}

# Main
case "${1}" in
    --uninstall|-u)
        uninstall
        ;;
    --help|-h)
        echo "Usage: $0 [--uninstall]"
        echo ""
        echo "Options:"
        echo "  --uninstall, -u    Remove the daemon"
        echo "  --help, -h         Show this help"
        echo ""
        ;;
    *)
        install
        ;;
esac
