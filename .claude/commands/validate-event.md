# Validate Event - Pre-Flight & Quality Checks

SQL patterns for validating events before Well insert, verifying post-load quality, and monitoring system health.

---

## Pre-Flight Validation

Run these checks BEFORE inserting events into the Well.

### Required Fields Present

```sql
SELECT
    CASE
        WHEN TENANT_ID IS NULL THEN 'FAIL: Missing TENANT_ID'
        WHEN SOURCE_SYSTEM IS NULL THEN 'FAIL: Missing SOURCE_SYSTEM'
        WHEN EVENT_TYPE IS NULL THEN 'FAIL: Missing EVENT_TYPE'
        WHEN EVENT_AT IS NULL THEN 'FAIL: Missing EVENT_AT'
        WHEN SUBJECT_ID IS NULL THEN 'FAIL: Missing SUBJECT_ID'
        ELSE 'PASS'
    END AS validation_status
FROM your_staging_table;
```

### Data Types Correct

```sql
-- EVENT_AT must be valid timestamp
SELECT COUNT(*) AS invalid_timestamps
FROM staging
WHERE TRY_CAST(EVENT_AT AS TIMESTAMP_LTZ) IS NULL;

-- Numeric fields must be numeric
SELECT COUNT(*) AS invalid_amounts
FROM staging
WHERE TRY_CAST(METADATA:amount AS NUMBER) IS NULL
  AND METADATA:amount IS NOT NULL;
```

### No Duplicates

```sql
SELECT EVENT_NK_HASH, COUNT(*) AS duplicate_count
FROM staging
GROUP BY EVENT_NK_HASH
HAVING COUNT(*) > 1;
```

### Event Type Naming Convention

```sql
-- Must contain underscore AND be uppercase (except dot notation for external systems)
SELECT EVENT_TYPE
FROM staging
WHERE (EVENT_TYPE NOT LIKE '%\_%' AND EVENT_TYPE NOT LIKE '%.%')
   OR (EVENT_TYPE != UPPER(EVENT_TYPE) AND EVENT_TYPE NOT LIKE '%.%');
```

---

## Post-Load Quality Checks

Run AFTER loading to verify data integrity.

### Record Count Match (Well vs Activity Stream)

```sql
SELECT
    'Well' AS source,
    COUNT(*) AS record_count,
    COUNT(DISTINCT EVENT_NK_HASH) AS unique_events
FROM DW_DEV_STREAM.LANDING.CORE_S00_RAW_EVENTS
WHERE INGESTED_AT >= :load_start_time

UNION ALL

SELECT
    'Activity Stream' AS source,
    COUNT(*) AS record_count,
    COUNT(DISTINCT EVENT_NK_HASH) AS unique_events
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
WHERE INGESTED_AT >= :load_start_time;
```

### Processing Lag by Source

```sql
SELECT
    SOURCE_SYSTEM,
    MAX(EVENT_AT) AS latest_event,
    MAX(INGESTED_AT) AS latest_ingestion,
    DATEDIFF('hour', MAX(EVENT_AT), CURRENT_TIMESTAMP()) AS lag_hours
FROM DW_DEV_STREAM.ACTIVITY.ACTIVITY_STREAM
GROUP BY SOURCE_SYSTEM
ORDER BY lag_hours DESC;
```

---

## Governance Health Dashboards

Built-in monitoring views - query these for system health.

### ETL Freshness

```sql
-- Lag hours by API source
SELECT * FROM DW_DEV_REPORT.RPT.ETL_S40_RPT_FRESHNESS;
```

### Volume Monitoring

```sql
-- Daily row counts by source system
SELECT * FROM DW_DEV_REPORT.RPT.ETL_S42_RPT_VOLUME_DAILY;
```

### Job Run History

```sql
-- Success/failure history of ETL jobs
SELECT * FROM DW_DEV_REPORT.RPT.ETL_S41_RPT_JOB_RUNS;
```

### Health Dashboard

```sql
-- Overall system health
SELECT * FROM DW_DEV_REPORT.RPT.ETL_S90_GOV_HEALTH_DASHBOARD;
```

---

## Quick Validation Checklist

Before any Well insert or batch load:

1. All rows have TENANT_ID, SOURCE_SYSTEM, EVENT_TYPE, EVENT_AT, SUBJECT_ID
2. EVENT_AT casts to TIMESTAMP_LTZ without error
3. Numeric METADATA fields are actual numbers
4. EVENT_NK_HASH has no duplicates within batch
5. EVENT_TYPE follows `OBJECT_ACTION` pattern (or dot notation for external systems)
6. SOURCE_SYSTEM is UPPERCASE
7. No core column values duplicated in METADATA

After load:

1. Well count matches expected batch size
2. Activity Stream count matches Well (after dynamic table refresh)
3. Processing lag is within acceptable SLA
4. No new duplicate EVENT_NK_HASH values across batches

---

## Related Skills

- `/activity-stream-guide` - Full schema, implementation rules, JSON template
- `/event-patterns` - System-specific metadata templates
- `/report` - Query DW_DEV_REPORT.RPT views
