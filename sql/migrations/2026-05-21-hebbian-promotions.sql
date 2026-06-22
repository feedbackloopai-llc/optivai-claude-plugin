-- Brain v0.2.0-neurosymbolic — Hebbian promotion primitive (brain-W1-S10)
-- Agent-controlled memory weighting with time-decay; the 4th MS_eps column.
-- Reference: Lin/Li/Chen 2026 §12.1; OptivAI builder memory_promotions table.
-- Non-destructive: pure CREATE IF NOT EXISTS.
BEGIN;

CREATE TABLE IF NOT EXISTS brain.promotions (
    promotion_id    BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES brain.thoughts(thought_id) ON DELETE CASCADE,
    user_id         VARCHAR(100)      NOT NULL,
    weight          DOUBLE PRECISION  NOT NULL DEFAULT 1.0,
    promoted_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    prov_agent      VARCHAR(100)      NOT NULL,
    reason          TEXT,
    UNIQUE (thought_id, user_id, promoted_at)
);

-- (thought_id, user_id) — the hot path for compute_effective_weight().
CREATE INDEX IF NOT EXISTS idx_promotions_thought_user ON brain.promotions (thought_id, user_id);
-- (user_id, promoted_at DESC) — recent-promotions-by-user dashboards.
CREATE INDEX IF NOT EXISTS idx_promotions_user_time ON brain.promotions (user_id, promoted_at DESC);

COMMIT;
