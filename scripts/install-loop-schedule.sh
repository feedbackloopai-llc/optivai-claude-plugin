#!/bin/bash
# install-loop-schedule.sh — Install (or uninstall) the OptivAI Loop runner LaunchAgent (T4)
#
# Usage:
#   ./install-loop-schedule.sh --label <label> --molecule <mol> \
#       --verify-cmd <cmd> --cadence <seconds> [--live]
#   ./install-loop-schedule.sh --uninstall <label>
#   ./install-loop-schedule.sh --help
#
# Safety:
#   Without --live the installed plist includes --dry-run so no live tokens
#   are spent and no beads are closed.  The bootstrap / enable commands are
#   PRINTED but NOT executed unless --live is passed.
#
# macOS note: cron does NOT work on this Mac (FDA gate).  Use launchd only.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/loop-schedule.plist.template"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${HOME}/.claude/logs"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    cat <<'EOF'
Usage:
  install-loop-schedule.sh --label LABEL --molecule MOL \
      --verify-cmd CMD --cadence SECONDS [--live]

  install-loop-schedule.sh --uninstall LABEL

  install-loop-schedule.sh --help

Options:
  --label LABEL        Short identifier (e.g. "daily-loop"). Used in the service
                       name: com.erato949.optivai-loop-<label>
  --molecule MOL       Molecule label passed to loop_runner.py --molecule
  --verify-cmd CMD     Verification command passed to loop_runner.py --verify-cmd
  --cadence SECONDS    How often to run (seconds). e.g. 3600 = hourly
  --live               Remove --dry-run and actually bootstrap the agent.
                       Without this flag the plist is written and the bootstrap
                       command is PRINTED but NOT executed.
  --uninstall LABEL    Bootout and remove the plist for <label>
  --help               Show this message

Safety note:
  The default (without --live) installs a plist with --dry-run so the scheduled
  runner prints plans but does NOT call claude, close beads, or spend tokens.
  Pass --live only when you are ready for autonomous operation.
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# resolve python3 — prefer the same interpreter the user has in $PATH
# ---------------------------------------------------------------------------
resolve_python() {
    if command -v python3 &>/dev/null; then
        python3_path="$(command -v python3)"
    else
        die "python3 not found in PATH. Install Python 3.8+ first."
    fi
}

# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------
uninstall() {
    local label="$1"
    local service_name="com.erato949.optivai-loop-${label}"
    local plist_dest="${LAUNCH_AGENTS_DIR}/${service_name}.plist"

    echo "Uninstalling OptivAI Loop agent: ${service_name}"
    echo ""

    # Bootout if loaded
    local uid
    uid="$(id -u)"
    if launchctl print "gui/${uid}/${service_name}" &>/dev/null 2>&1; then
        launchctl bootout "gui/${uid}" "${plist_dest}" 2>/dev/null || true
        ok "Service booted out: ${service_name}"
    else
        warn "Service was not loaded (nothing to bootout): ${service_name}"
    fi

    # Remove plist
    if [[ -f "${plist_dest}" ]]; then
        rm "${plist_dest}"
        ok "Removed: ${plist_dest}"
    else
        warn "Plist not found at ${plist_dest}"
    fi

    echo ""
    ok "Uninstall complete for label '${label}'"
}

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
install() {
    local label="$1"
    local molecule="$2"
    local verify_cmd="$3"
    local cadence="$4"
    local live="$5"   # "true" or "false"

    local service_name="com.erato949.optivai-loop-${label}"
    local plist_dest="${LAUNCH_AGENTS_DIR}/${service_name}.plist"
    local loop_runner="${SCRIPT_DIR}/loop_runner.py"

    echo "Installing OptivAI Loop LaunchAgent: ${service_name}"
    echo ""

    # --- Prerequisites ---
    echo "Checking prerequisites..."

    [[ -f "${TEMPLATE}" ]] || die "Template not found: ${TEMPLATE}"
    ok "Template found: ${TEMPLATE}"

    [[ -f "${loop_runner}" ]] || die "loop_runner.py not found: ${loop_runner}"
    ok "loop_runner.py found: ${loop_runner}"

    resolve_python
    ok "python3: ${python3_path} ($(python3 --version 2>&1))"

    # Validate cadence is a positive integer
    if ! [[ "${cadence}" =~ ^[1-9][0-9]*$ ]]; then
        die "--cadence must be a positive integer (seconds). Got: '${cadence}'"
    fi
    ok "cadence: ${cadence}s"

    # --- Create dirs ---
    echo ""
    echo "Creating directories..."
    mkdir -p "${LAUNCH_AGENTS_DIR}"
    mkdir -p "${LOGS_DIR}"
    ok "Directories ready"

    # --- Build EXTRA_FLAGS fragment for ProgramArguments ---
    # In dry-run mode (default): add <string>--dry-run</string>
    # In live mode: no extra flag (omit --dry-run entirely)
    local extra_flags_xml
    if [[ "${live}" == "true" ]]; then
        extra_flags_xml=""
        warn "LIVE MODE: --dry-run NOT set — runner will dispatch real claude calls and close beads"
    else
        extra_flags_xml="<string>--dry-run</string>"
        ok "DRY-RUN default: schedule will NOT spend tokens or close beads until --live is used"
    fi

    # --- Fill template via sed ---
    # We use a temp file then move to avoid partial writes.
    echo ""
    echo "Writing plist..."
    local tmp_plist
    tmp_plist="$(mktemp /tmp/optivai-loop-plist.XXXXXX)"

    # escape_sed: escape characters that break sed's replacement string
    escape_sed() {
        printf '%s' "$1" | sed 's/[&/\]/\\&/g'
    }

    local esc_label esc_python esc_loop_runner esc_molecule esc_verify_cmd esc_cadence esc_home esc_extra_flags
    esc_label="$(escape_sed "${label}")"
    esc_python="$(escape_sed "${python3_path}")"
    esc_loop_runner="$(escape_sed "${loop_runner}")"
    esc_molecule="$(escape_sed "${molecule}")"
    esc_verify_cmd="$(escape_sed "${verify_cmd}")"
    esc_cadence="$(escape_sed "${cadence}")"
    esc_home="$(escape_sed "${HOME}")"
    esc_extra_flags="$(escape_sed "${extra_flags_xml}")"

    sed \
        -e "s/{LABEL}/${esc_label}/g" \
        -e "s|{PYTHON}|${esc_python}|g" \
        -e "s|{LOOP_RUNNER}|${esc_loop_runner}|g" \
        -e "s/{MOLECULE}/${esc_molecule}/g" \
        -e "s|{VERIFY_CMD}|${esc_verify_cmd}|g" \
        -e "s/{CADENCE_SECONDS}/${esc_cadence}/g" \
        -e "s|{HOME}|${esc_home}|g" \
        -e "s|        {EXTRA_FLAGS}|        ${esc_extra_flags}|g" \
        "${TEMPLATE}" > "${tmp_plist}"

    mv "${tmp_plist}" "${plist_dest}"
    ok "Plist written: ${plist_dest}"

    # --- Validate ---
    if ! plutil -lint "${plist_dest}" >/dev/null 2>&1; then
        err "Plist validation FAILED:"
        plutil -lint "${plist_dest}"
        rm -f "${plist_dest}"
        die "Aborting — plist removed"
    fi
    ok "plutil -lint: OK"

    # --- Bootstrap or just print ---
    local uid
    uid="$(id -u)"
    local bootstrap_cmd="launchctl bootstrap gui/${uid} ${plist_dest}"
    local enable_cmd="launchctl enable gui/${uid}/${service_name}"

    echo ""
    if [[ "${live}" == "true" ]]; then
        echo "Bootstrapping service (--live)..."

        # Bootout first if already loaded (idempotent re-install)
        if launchctl print "gui/${uid}/${service_name}" &>/dev/null 2>&1; then
            launchctl bootout "gui/${uid}" "${plist_dest}" 2>/dev/null || true
            ok "Previous instance booted out"
        fi

        ${bootstrap_cmd}
        ok "Bootstrapped: ${service_name}"

        ${enable_cmd}
        ok "Enabled: ${service_name}"

        echo ""
        echo "============================================"
        ok "Installation complete (LIVE)!"
        echo "============================================"
        echo ""
        echo "Service:  ${service_name}"
        echo "Cadence:  every ${cadence}s"
        echo "Molecule: ${molecule}"
        echo "Log:      ${LOGS_DIR}/loop-${label}.log"
        echo ""
        echo "Commands:"
        echo "  Status : launchctl print gui/${uid}/${service_name}"
        echo "  Stop   : launchctl bootout gui/${uid} ${plist_dest}"
        echo "  Logs   : tail -f ${LOGS_DIR}/loop-${label}.log"
        echo "  Remove : ${SCRIPT_DIR}/install-loop-schedule.sh --uninstall ${label}"
    else
        echo "============================================"
        ok "Plist installed (DRY-RUN default — NOT bootstrapped)"
        echo "============================================"
        echo ""
        echo "The schedule is written but NOT loaded. The runner will use --dry-run"
        echo "(no live claude calls, no beads closed, no token spend) until you"
        echo "re-install with --live."
        echo ""
        echo "To enable later, run:"
        echo ""
        echo "  # Re-install in live mode:"
        echo "  ${SCRIPT_DIR}/install-loop-schedule.sh \\"
        echo "      --label ${label} --molecule ${molecule} \\"
        echo "      --verify-cmd '${verify_cmd}' --cadence ${cadence} --live"
        echo ""
        echo "  # OR manually bootstrap the dry-run plist (still uses --dry-run):"
        echo "  ${bootstrap_cmd}"
        echo "  ${enable_cmd}"
        echo ""
        echo "To remove the plist:"
        echo "  ${SCRIPT_DIR}/install-loop-schedule.sh --uninstall ${label}"
    fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    usage
fi

MODE=""
LABEL=""
MOLECULE=""
VERIFY_CMD=""
CADENCE=""
LIVE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --label)
            [[ $# -ge 2 ]] || die "--label requires an argument"
            LABEL="$2"; shift 2 ;;
        --molecule)
            [[ $# -ge 2 ]] || die "--molecule requires an argument"
            MOLECULE="$2"; shift 2 ;;
        --verify-cmd)
            [[ $# -ge 2 ]] || die "--verify-cmd requires an argument"
            VERIFY_CMD="$2"; shift 2 ;;
        --cadence)
            [[ $# -ge 2 ]] || die "--cadence requires an argument"
            CADENCE="$2"; shift 2 ;;
        --live)
            LIVE="true"; shift ;;
        --uninstall)
            [[ $# -ge 2 ]] || die "--uninstall requires a LABEL argument"
            MODE="uninstall"
            LABEL="$2"; shift 2 ;;
        --help|-h)
            usage ;;
        *)
            die "Unknown argument: $1. Run with --help for usage." ;;
    esac
done

# Dispatch
if [[ "${MODE}" == "uninstall" ]]; then
    [[ -n "${LABEL}" ]] || die "--uninstall requires a label"
    uninstall "${LABEL}"
    exit 0
fi

# Install mode — validate required args
[[ -n "${LABEL}" ]]      || die "--label is required"
[[ -n "${MOLECULE}" ]]   || die "--molecule is required"
[[ -n "${VERIFY_CMD}" ]] || die "--verify-cmd is required"
[[ -n "${CADENCE}" ]]    || die "--cadence is required"

install "${LABEL}" "${MOLECULE}" "${VERIFY_CMD}" "${CADENCE}" "${LIVE}"
