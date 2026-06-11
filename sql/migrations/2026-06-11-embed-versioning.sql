-- Migration: add embed_model + embed_dim versioning columns to brain.thoughts
-- and brain.thought_versions.
--
-- Problem: EMBED_MODEL was a hardcoded constant with no per-row record.
-- Changing the model silently produces meaningless cosine similarity between
-- old (model A) and new (model B) vectors.  Dimension change causes INSERT
-- failures or garbage.
--
-- Fix: stamp each row with the model name and dimension so search() can
-- filter to the current model's vectors.  Existing rows are backfilled with
-- the known-good model ('all-mpnet-base-v2', 768) since all were produced
-- by that model.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, UPDATE ... WHERE embed_model IS NULL.
-- Applied live: python3 scripts/open_brain.py --migrate sql/migrations/2026-06-11-embed-versioning.sql
-- fblai-3yd1j

-- brain.thoughts ─────────────────────────────────────────────────────────────

ALTER TABLE brain.thoughts
    ADD COLUMN IF NOT EXISTS embed_model VARCHAR(100),
    ADD COLUMN IF NOT EXISTS embed_dim   SMALLINT;

-- Backfill: all existing rows were produced by all-mpnet-base-v2 (768-dim).
-- The WHERE guard makes this re-runnable; newly captured rows already have
-- embed_model set so they are excluded.
UPDATE brain.thoughts
SET
    embed_model = 'all-mpnet-base-v2',
    embed_dim   = 768
WHERE embed_model IS NULL
  AND embedding  IS NOT NULL;

-- brain.thought_versions ──────────────────────────────────────────────────────

ALTER TABLE brain.thought_versions
    ADD COLUMN IF NOT EXISTS embed_model VARCHAR(100),
    ADD COLUMN IF NOT EXISTS embed_dim   SMALLINT;

UPDATE brain.thought_versions
SET
    embed_model = 'all-mpnet-base-v2',
    embed_dim   = 768
WHERE embed_model IS NULL
  AND embedding  IS NOT NULL;
