-- Brain v0.2.0-neurosymbolic — RB primitive (brain-W1-S4)
-- W3C PROV-DM 1.3 versioning substrate; snapshot/rollback/diff operate on this table.
-- Non-destructive: pure CREATE IF NOT EXISTS.
BEGIN;

CREATE TABLE IF NOT EXISTS brain.thought_versions (
    version_id      BIGSERIAL         NOT NULL PRIMARY KEY,
    thought_id      VARCHAR(64)       NOT NULL REFERENCES brain.thoughts(thought_id) ON DELETE CASCADE,
    revision        INTEGER           NOT NULL,                 -- monotonic per thought_id starting at 1
    raw_text        TEXT              NOT NULL,
    summary         VARCHAR(1000),
    thought_type    VARCHAR(50),
    topics          JSONB,
    people          JSONB,
    action_items    JSONB,
    embedding       vector(768),
    metadata        JSONB,
    -- PROV-DM at the version level: every snapshot is its own PROV event
    prov_agent      VARCHAR(100)      NOT NULL,
    prov_activity   VARCHAR(50)       NOT NULL,                 -- 'snapshot' | 'rollback' | 'update'
    parent_version  BIGINT            REFERENCES brain.thought_versions(version_id),
    diff_json       JSONB,                                       -- RFC 6902 patch from parent_version -> this
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    UNIQUE (thought_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_thought_versions_thought ON brain.thought_versions (thought_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_thought_versions_created ON brain.thought_versions (created_at DESC);

COMMIT;
