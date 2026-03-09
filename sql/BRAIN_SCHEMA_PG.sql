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
    session_id      VARCHAR(200),
    project         VARCHAR(200),
    embedding       vector(768),
    metadata        JSONB,
    created_at      TIMESTAMPTZ       DEFAULT NOW(),
    updated_at      TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_thoughts_user_created ON thoughts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_thoughts_type ON thoughts (user_id, thought_type);

-- IVFFlat index for vector similarity search
-- Note: IVFFlat requires data to exist for training. Create after initial data load if needed.
-- For small datasets (<10k rows), exact search is fine. Add this later:
-- CREATE INDEX IF NOT EXISTS idx_thoughts_embedding ON thoughts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

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
