-- Snowflake DDL for Claude Code Activity Logging
-- Run this in your Snowflake database to create the required table

-- Create schema if not exists (adjust database name as needed)
-- CREATE SCHEMA IF NOT EXISTS CLAUDE_LOGS;

-- Activity log table
CREATE TABLE IF NOT EXISTS CLAUDE_ACTIVITY_LOG (
    -- Precomputed time fields for efficient querying
    epoch           NUMBER          NOT NULL,       -- Unix timestamp (seconds)
    log_date        DATE            NOT NULL,       -- YYYY-MM-DD for daily filtering
    log_year        NUMBER(4)       NOT NULL,       -- Year for yearly aggregation
    log_month       NUMBER(2)       NOT NULL,       -- Month (1-12)
    log_day         NUMBER(2)       NOT NULL,       -- Day of month (1-31)
    log_hour        NUMBER(2)       NOT NULL,       -- Hour (0-23)

    -- Timestamps
    timestamp_utc   TIMESTAMP_NTZ   NOT NULL,       -- Full UTC timestamp
    time_local      VARCHAR(8),                     -- Local time HH:MM:SS

    -- Core activity fields
    operation       VARCHAR(50)     NOT NULL,       -- Operation type (read, write, bash, etc.)
    prompt          VARCHAR(2000),                  -- Human-readable description
    session_id      VARCHAR(100),                   -- Session identifier

    -- Context
    cwd             VARCHAR(500),                   -- Working directory
    project         VARCHAR(100),                   -- Project name

    -- Tool-specific metadata (semi-structured)
    details         VARIANT,                        -- JSON object with tool details

    -- Sync metadata
    source_file     VARCHAR(100),                   -- Source log file name
    source_line     NUMBER,                         -- Line number in source file
    synced_at       TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP()
);

-- Add comments for documentation
COMMENT ON TABLE CLAUDE_ACTIVITY_LOG IS 'Claude Code agent activity log - synced from local JSON logs';
COMMENT ON COLUMN CLAUDE_ACTIVITY_LOG.epoch IS 'Unix timestamp for time-range queries';
COMMENT ON COLUMN CLAUDE_ACTIVITY_LOG.details IS 'Tool-specific metadata as JSON (command, file_path, pattern, etc.)';

-- Clustering for query performance (optional but recommended)
-- ALTER TABLE CLAUDE_ACTIVITY_LOG CLUSTER BY (log_date, project);

-- Example queries:

-- Last hour of activity
-- SELECT * FROM CLAUDE_ACTIVITY_LOG
-- WHERE epoch > (SELECT EXTRACT(EPOCH FROM CURRENT_TIMESTAMP()) - 3600);

-- Today's bash commands
-- SELECT * FROM CLAUDE_ACTIVITY_LOG
-- WHERE log_date = CURRENT_DATE() AND operation = 'bash';

-- Activity by hour for a specific project
-- SELECT log_hour, COUNT(*) as operations
-- FROM CLAUDE_ACTIVITY_LOG
-- WHERE project = 'my-project' AND log_date = CURRENT_DATE()
-- GROUP BY log_hour ORDER BY log_hour;

-- Extract details from VARIANT column
-- SELECT operation, details:command::STRING as command
-- FROM CLAUDE_ACTIVITY_LOG
-- WHERE operation = 'bash';
