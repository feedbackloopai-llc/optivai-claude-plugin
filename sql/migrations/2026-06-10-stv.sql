-- Brain NAL-lite truth-value layer (T2.6 / fblai-eovhe)
-- Adds stv_frequency + stv_confidence to brain.thoughts and brain.thought_versions.
-- Idempotent: ADD COLUMN IF NOT EXISTS, guarded DO block for backfill.
-- Migration date: 2026-06-10
BEGIN;

-- ── brain.thoughts ──────────────────────────────────────────────────────────
ALTER TABLE brain.thoughts
    ADD COLUMN IF NOT EXISTS stv_frequency   REAL NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS stv_confidence  REAL NOT NULL DEFAULT 0.5;

-- ── brain.thought_versions ───────────────────────────────────────────────────
ALTER TABLE brain.thought_versions
    ADD COLUMN IF NOT EXISTS stv_frequency   REAL NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS stv_confidence  REAL NOT NULL DEFAULT 0.5;

-- ── Backfill existing thoughts from metadata->>'confidence' ──────────────────
-- Maps high→0.9, medium→0.7, low→0.5, NULL/absent/other→0.5
-- Only touches rows where stv_confidence is still at the default (0.5) AND
-- the metadata column carries a recognised confidence label, so repeated runs
-- are a no-op.
DO $$
BEGIN
    UPDATE brain.thoughts
    SET stv_confidence = CASE metadata->>'confidence'
            WHEN 'high'   THEN 0.9
            WHEN 'medium' THEN 0.7
            WHEN 'low'    THEN 0.5
            ELSE               0.5
        END,
        stv_frequency  = 1.0
    WHERE stv_confidence = 0.5;  -- default value — safe to overwrite
END;
$$;

-- ── Backfill existing thought_versions ───────────────────────────────────────
-- Versions carry metadata JSONB just like thoughts; same mapping.
DO $$
BEGIN
    UPDATE brain.thought_versions
    SET stv_confidence = CASE metadata->>'confidence'
            WHEN 'high'   THEN 0.9
            WHEN 'medium' THEN 0.7
            WHEN 'low'    THEN 0.5
            ELSE               0.5
        END,
        stv_frequency  = 1.0
    WHERE stv_confidence = 0.5;
END;
$$;

COMMIT;
