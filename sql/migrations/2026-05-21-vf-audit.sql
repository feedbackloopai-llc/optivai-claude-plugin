-- Brain v0.2.0-neurosymbolic — VF_eps audit log (brain-W1-S8)
-- Procurement-grade audit trail for every --forget invocation.
-- Records BOTH Hoeffding (loose) and exact-binomial (tight) bounds per R2 fix-wave.
-- Non-destructive: pure CREATE IF NOT EXISTS.
BEGIN;

CREATE TABLE IF NOT EXISTS brain.forget_audit (
    audit_id              BIGSERIAL         NOT NULL PRIMARY KEY,
    forgotten_thought_id  VARCHAR(64)       NOT NULL,
    user_id               VARCHAR(100)      NOT NULL,
    status                VARCHAR(20)       NOT NULL,           -- 'forgotten' | 'forget-failed-residue' | 'forget-failed-error'
    n                     INTEGER           NOT NULL,
    k                     INTEGER           NOT NULL,
    epsilon               DOUBLE PRECISION  NOT NULL,
    -- BOTH bounds recorded distinctly (R2 fix-wave)
    hoeffding_bound       DOUBLE PRECISION  NOT NULL,
    hoeffding_confidence  DOUBLE PRECISION  NOT NULL,
    exact_binomial_bound  DOUBLE PRECISION  NOT NULL,
    exact_binomial_conf   DOUBLE PRECISION  NOT NULL,
    -- Probe quality marker per R3 fix-wave
    probe_quality_json    JSONB             NOT NULL,           -- {n, distribution, sampledFromSnapshot}
    -- PROV-DM
    prov_agent            VARCHAR(100)      NOT NULL,
    prov_activity         VARCHAR(50)       NOT NULL DEFAULT 'forget',
    -- For forget-failed cases, optional error/diagnostic context
    diagnostic_json       JSONB,
    created_at            TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forget_audit_user_time ON brain.forget_audit (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forget_audit_thought ON brain.forget_audit (forgotten_thought_id);
CREATE INDEX IF NOT EXISTS idx_forget_audit_status ON brain.forget_audit (status);

COMMIT;
