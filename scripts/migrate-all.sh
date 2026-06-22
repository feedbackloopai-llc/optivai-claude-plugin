#!/bin/bash
# scripts/migrate-all.sh — Idempotent runner for all brain-v0.2.0 migrations.
#
# Runs migrations in Lin/Li/Chen §12.1 dependency order:
#   PV (PROV-DM) → RB (versions) → VF_ε (audit) → Hebbian (promotions) → Replay log.
#
# Each migration uses CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS /
# DO-block guards so re-runs are no-ops.
#
# Usage: bash scripts/migrate-all.sh
# Requires: DATABASE_URL env var.
#
# Bead: deploy-S1 (brain-v0.2.0 deployment closure).

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "Error: DATABASE_URL not set" >&2
  exit 1
fi

# Script directory + repo root resolution (handles symlinked installs)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MIGRATIONS_DIR="$REPO_DIR/sql/migrations"

if [[ ! -d "$MIGRATIONS_DIR" ]]; then
  echo "Error: migrations dir not found at $MIGRATIONS_DIR" >&2
  exit 1
fi

# Dependency order per Lin/Li/Chen §12.1 (VF_ε ⪯ RB ⪯ PV ⪯ WA)
MIGRATIONS=(
  "2026-05-21-prov-dm.sql"             # PV — foundational, must run first
  "2026-05-21-rb-versions.sql"         # RB — depends on PV's column shape
  "2026-05-21-vf-audit.sql"            # VF audit — references existing tables
  "2026-05-21-hebbian-promotions.sql"  # Hebbian — parallel-safe; ordered for determinism
  "2026-05-21-replay-log.sql"          # Replay log — last; instruments all prior tables
)

PYTHON_CMD="${PYTHON_CMD:-python3}"
BRAIN_SCRIPT="$REPO_DIR/scripts/open_brain.py"

if [[ ! -f "$BRAIN_SCRIPT" ]]; then
  echo "Error: open_brain.py not found at $BRAIN_SCRIPT" >&2
  exit 1
fi

echo "Running 5 migrations in dependency order..."
for m in "${MIGRATIONS[@]}"; do
  MIGRATION_PATH="$MIGRATIONS_DIR/$m"
  if [[ ! -f "$MIGRATION_PATH" ]]; then
    echo "Error: migration file missing: $MIGRATION_PATH" >&2
    exit 1
  fi
  echo "  → $m"
  "$PYTHON_CMD" "$BRAIN_SCRIPT" --migrate "$MIGRATION_PATH"
done

echo "✓ All 5 migrations complete. Brain v0.2.0 schema operational."
