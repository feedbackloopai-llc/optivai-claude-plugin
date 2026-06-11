-- stv_seeded sentinel column (fblai-3zk83)
-- Prevents re-running the T2.6 backfill from clobbering atoms whose
-- stv_confidence was deliberately set to 0.5 via --stv-c by the user.
--
-- Problem: 2026-06-10-stv.sql backfills stv_confidence with a guard
-- `WHERE stv_confidence = 0.5`. A user can set stv_confidence=0.5 via
-- `--stv-c 0.5`. If the migration is re-run, that deliberate 0.5 would
-- be re-backfilled by the confidence-label default (e.g. 0.9 for 'high'),
-- clobbering the intentional override.
--
-- Fix: add stv_seeded BOOLEAN NOT NULL DEFAULT FALSE. capture() sets it
-- TRUE on every new INSERT (the stv is intentionally set at creation time
-- and must never be re-backfilled). This migration also marks all EXISTING
-- rows TRUE (they were already seeded — either by the original T2.6 backfill
-- or by a post-T2.6 capture). Future re-runs of T2.6 can safely guard on
-- `WHERE stv_seeded = FALSE` instead of `WHERE stv_confidence = 0.5`.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + UPDATE WHERE stv_seeded = FALSE.
-- Second run of the UPDATE touches zero rows.
-- Migration date: 2026-06-11

BEGIN;

-- ── brain.thoughts ────────────────────────────────────────────────────────────
ALTER TABLE brain.thoughts
    ADD COLUMN IF NOT EXISTS stv_seeded BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill all existing rows as seeded — their stv is settled.
UPDATE brain.thoughts SET stv_seeded = TRUE WHERE stv_seeded = FALSE;

-- ── brain.thought_versions ────────────────────────────────────────────────────
ALTER TABLE brain.thought_versions
    ADD COLUMN IF NOT EXISTS stv_seeded BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill existing version rows as seeded.
UPDATE brain.thought_versions SET stv_seeded = TRUE WHERE stv_seeded = FALSE;

COMMIT;
