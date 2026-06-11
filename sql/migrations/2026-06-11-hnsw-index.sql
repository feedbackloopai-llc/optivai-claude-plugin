-- Migration: add HNSW vector index on brain.thoughts.embedding
-- Replaces the commented-out IVFFlat stub (which was never active).
-- HNSW builds at any table size (zero rows is fine) and supports
-- incremental inserts — no training data required.
-- Idempotent: CREATE INDEX IF NOT EXISTS.
-- Note: do NOT SET search_path here — setting it before CREATE INDEX
-- causes psycopg2 multi-statement execute to lose the operator class
-- lookup context.  Use fully-qualified brain.thoughts instead.
-- Applied live: python3 scripts/open_brain.py --migrate sql/migrations/2026-06-11-hnsw-index.sql
-- fblai-3yd1j

CREATE INDEX IF NOT EXISTS idx_thoughts_embedding_hnsw
    ON brain.thoughts
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
