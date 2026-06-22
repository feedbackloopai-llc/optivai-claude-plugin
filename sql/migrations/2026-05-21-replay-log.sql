-- Brain v0.2.0-neurosymbolic — Replay log (brain-W2-S5)
-- Durable PII-distinct OTel-correlated audit trail of every brain operation.
-- Reference: optivai-builder W3-REPLAY-LOG (HARN-L704 + Scorecard #7).
-- Non-destructive: pure CREATE IF NOT EXISTS.
BEGIN;

CREATE TABLE IF NOT EXISTS brain.replay_log (
    event_id        BIGSERIAL         NOT NULL PRIMARY KEY,
    user_id         VARCHAR(100)      NOT NULL,
    session_id      VARCHAR(200),                          -- nullable for CLI-direct calls
    event_type      VARCHAR(50)       NOT NULL,            -- 'capture'|'search'|'forget'|'promote'|'demote'|'rollback'|'inspect'|'trace'|'snapshot'
    thought_id      VARCHAR(64),                           -- subject of the event (nullable)
    query_redacted  TEXT,                                  -- search/forget query with PII redacted
    result_summary  TEXT,                                  -- <=100-char preview, PII-redacted
    pii_distinct    BOOLEAN           NOT NULL DEFAULT TRUE,  -- PII redaction discipline marker
    trace_id        VARCHAR(64),                           -- OTel trace correlation (optional; from OTEL_TRACE_ID env)
    span_id         VARCHAR(64),                           -- OTel span correlation (optional)
    prov_agent      VARCHAR(100)      NOT NULL,
    metadata        JSONB,                                 -- type-specific extra fields
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

-- (session_id, created_at) — replay reconstruction for one session.
CREATE INDEX IF NOT EXISTS idx_replay_log_session
  ON brain.replay_log (session_id, created_at);
-- (user_id, created_at DESC) — per-user chronological dashboard hot path.
CREATE INDEX IF NOT EXISTS idx_replay_log_user_time
  ON brain.replay_log (user_id, created_at DESC);
-- (thought_id) — "all ops touching thought X" lookup.
CREATE INDEX IF NOT EXISTS idx_replay_log_thought
  ON brain.replay_log (thought_id) WHERE thought_id IS NOT NULL;
-- (event_type, created_at DESC) — recent-by-type filter for --event-type CLI.
CREATE INDEX IF NOT EXISTS idx_replay_log_event_type
  ON brain.replay_log (event_type, created_at DESC);

COMMIT;
