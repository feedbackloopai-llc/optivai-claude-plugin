-- Open Brain Schema: PostgreSQL + pgvector adaptation
-- Neon free tier with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS brain;

SET search_path TO brain;

CREATE TABLE IF NOT EXISTS thoughts (
    thought_id      VARCHAR(64)       NOT NULL PRIMARY KEY,
    user_id         VARCHAR(100)      NOT NULL,
    raw_text        TEXT              NOT NULL,
    summary         VARCHAR(1000),
    thought_type    VARCHAR(50),
    topics          JSONB             DEFAULT '[]'::jsonb,
    people          JSONB             DEFAULT '[]'::jsonb,
    action_items    JSONB             DEFAULT '[]'::jsonb,
    source          VARCHAR(50)       DEFAULT 'manual',
    -- PROV-DM 1.3 columns (W3C standard) — see sql/migrations/2026-05-21-prov-dm.sql
    -- Required (callers MUST supply): prov_agent, prov_activity, was_generated_by
    -- Optional: was_derived_from (soft FK to thought_id), source_uri
    prov_agent       VARCHAR(100)     NOT NULL,
    prov_activity    VARCHAR(50)      NOT NULL,
    was_generated_by VARCHAR(64)      NOT NULL,
    was_derived_from VARCHAR(64),
    source_uri       TEXT,
    session_id      VARCHAR(200),
    project         VARCHAR(200),
    embedding       vector(768),
    metadata        JSONB,
    created_at      TIMESTAMPTZ       DEFAULT NOW(),
    updated_at      TIMESTAMPTZ       DEFAULT NOW(),
    CONSTRAINT fk_thoughts_derived_from
      FOREIGN KEY (was_derived_from) REFERENCES thoughts(thought_id)
      ON DELETE SET NULL
      DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_thoughts_user_created ON thoughts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_thoughts_type ON thoughts (user_id, thought_type);
-- PROV-DM citation-walker indexes (Wave-2 W2-S1 consumer)
CREATE INDEX IF NOT EXISTS idx_thoughts_derived_from
  ON thoughts (was_derived_from) WHERE was_derived_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_thoughts_generated_by
  ON thoughts (was_generated_by);

-- IVFFlat index for vector similarity search
-- Note: IVFFlat requires data to exist for training. Create after initial data load if needed.
-- For small datasets (<10k rows), exact search is fine. Add this later:
-- CREATE INDEX IF NOT EXISTS idx_thoughts_embedding ON thoughts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================================
-- RB primitive (brain-W1-S4): versioning substrate for snapshot/rollback/diff
-- See sql/migrations/2026-05-21-rb-versions.sql for the migration version.
-- W3C PROV-DM 1.3 conformant at the version level (per-snapshot PROV event).
-- ON DELETE CASCADE: forgetting a thought (VF_eps) removes its versions too.
-- ============================================================================
CREATE TABLE IF NOT EXISTS thought_versions (
    version_id      BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES thoughts(thought_id) ON DELETE CASCADE,
    revision        INTEGER           NOT NULL,
    raw_text        TEXT              NOT NULL,
    summary         VARCHAR(1000),
    thought_type    VARCHAR(50),
    topics          JSONB,
    people          JSONB,
    action_items    JSONB,
    embedding       vector(768),
    metadata        JSONB,
    prov_agent      VARCHAR(100)      NOT NULL,
    prov_activity   VARCHAR(50)       NOT NULL,
    parent_version  BIGINT            REFERENCES thought_versions(version_id),
    diff_json       JSONB,
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    UNIQUE (thought_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_thought_versions_thought ON thought_versions (thought_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_thought_versions_created ON thought_versions (created_at DESC);

-- ============================================================================
-- VF_eps audit log (brain-W1-S8): procurement-grade trail for every --forget.
-- See sql/migrations/2026-05-21-vf-audit.sql for the migration version.
-- Records BOTH Hoeffding (loose) and exact-binomial (tight) bounds (R2 fix-wave).
-- ============================================================================
CREATE TABLE IF NOT EXISTS forget_audit (
    audit_id              BIGSERIAL         NOT NULL PRIMARY KEY,
    forgotten_thought_id  VARCHAR(64)       NOT NULL,
    user_id               VARCHAR(100)      NOT NULL,
    status                VARCHAR(40)       NOT NULL,
    n                     INTEGER           NOT NULL,
    k                     INTEGER           NOT NULL,
    epsilon               DOUBLE PRECISION  NOT NULL,
    hoeffding_bound       DOUBLE PRECISION  NOT NULL,
    hoeffding_confidence  DOUBLE PRECISION  NOT NULL,
    exact_binomial_bound  DOUBLE PRECISION  NOT NULL,
    exact_binomial_conf   DOUBLE PRECISION  NOT NULL,
    probe_quality_json    JSONB             NOT NULL,
    prov_agent            VARCHAR(100)      NOT NULL,
    prov_activity         VARCHAR(50)       NOT NULL DEFAULT 'forget',
    diagnostic_json       JSONB,
    created_at            TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forget_audit_user_time ON forget_audit (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forget_audit_thought ON forget_audit (forgotten_thought_id);
CREATE INDEX IF NOT EXISTS idx_forget_audit_status ON forget_audit (status);

-- ============================================================================
-- Hebbian promotion primitive (brain-W1-S10): agent-controlled memory
-- weighting with time-decay — the 4th MS_eps column (after PV, RB, VF_eps).
-- See sql/migrations/2026-05-21-hebbian-promotions.sql for the migration.
-- Reference: Lin/Li/Chen 2026 §12.1; OptivAI builder memory_promotions table.
-- ON DELETE CASCADE: forgetting a thought (VF_eps) removes its promotions too.
-- Demote works by INSERTING a row with NEGATIVE weight (NOT deleting positives)
-- so the audit trail is preserved.
-- Decay formula (compute_effective_weight): sum(weight * (1+days_since)^(-0.7)).
-- ============================================================================
CREATE TABLE IF NOT EXISTS promotions (
    promotion_id    BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES thoughts(thought_id) ON DELETE CASCADE,
    user_id         VARCHAR(100)      NOT NULL,
    weight          DOUBLE PRECISION  NOT NULL DEFAULT 1.0,
    promoted_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    prov_agent      VARCHAR(100)      NOT NULL,
    reason          TEXT,
    UNIQUE (thought_id, user_id, promoted_at)
);

CREATE INDEX IF NOT EXISTS idx_promotions_thought_user ON promotions (thought_id, user_id);
CREATE INDEX IF NOT EXISTS idx_promotions_user_time ON promotions (user_id, promoted_at DESC);

-- Landing schema for activity logs
CREATE SCHEMA IF NOT EXISTS landing;

CREATE TABLE IF NOT EXISTS landing.raw_events (
    event_id        VARCHAR(200)      NOT NULL,
    tenant_id       VARCHAR(50)       NOT NULL DEFAULT 'CLAUDE_CODE',
    source_system   VARCHAR(50)       NOT NULL DEFAULT 'CLAUDE_CODE',
    event_type      VARCHAR(100)      NOT NULL,
    event_at        TIMESTAMPTZ       NOT NULL,
    actor_id        VARCHAR(200)      NOT NULL,
    actor_type      VARCHAR(50)       NOT NULL DEFAULT 'BOT',
    subject_id      VARCHAR(200),
    subject_type    VARCHAR(50),
    metadata        JSONB,
    ingested_at     TIMESTAMPTZ       DEFAULT NOW(),
    event_nk_hash   VARCHAR(64),
    PRIMARY KEY (event_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_events_type ON landing.raw_events (event_type, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_actor ON landing.raw_events (actor_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_hash ON landing.raw_events (event_nk_hash);
