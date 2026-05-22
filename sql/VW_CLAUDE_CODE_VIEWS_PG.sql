-- Brain views for PostgreSQL

SET search_path TO brain;

-- User stats view
-- Defensive: jsonb_array_length() errors on scalar JSONB. Some captures from
-- LLM metadata extraction can return non-array values (e.g., {"people": null}
-- becomes JSONB scalar null instead of []). Guard with jsonb_typeof = 'array'.
CREATE OR REPLACE VIEW v_user_stats AS
SELECT
    user_id,
    COUNT(*) AS total_thoughts,
    COUNT(DISTINCT thought_type) AS distinct_types,
    MIN(created_at) AS first_thought,
    MAX(created_at) AS latest_thought,
    EXTRACT(DAY FROM MAX(created_at) - MIN(created_at))::int AS active_days,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS thoughts_this_week,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS thoughts_this_month,
    COUNT(*) FILTER (
        WHERE jsonb_typeof(action_items) = 'array'
          AND jsonb_array_length(action_items) > 0
    ) AS thoughts_with_actions,
    COUNT(*) FILTER (
        WHERE jsonb_typeof(people) = 'array'
          AND jsonb_array_length(people) > 0
    ) AS thoughts_with_people
FROM thoughts
GROUP BY user_id;

-- Topic frequency per user
-- Defensive: jsonb_array_elements_text() errors on scalar JSONB. Same guard.
CREATE OR REPLACE VIEW v_user_topics AS
SELECT
    t.user_id,
    topic.value::text AS topic,
    COUNT(*) AS mention_count,
    MAX(t.created_at) AS last_mentioned
FROM thoughts t,
    jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(t.topics) = 'array' THEN t.topics ELSE '[]'::jsonb END
    ) AS topic(value)
GROUP BY t.user_id, topic.value::text;

-- People frequency per user
CREATE OR REPLACE VIEW v_user_people AS
SELECT
    t.user_id,
    person.value::text AS person,
    COUNT(*) AS mention_count,
    MAX(t.created_at) AS last_mentioned
FROM thoughts t,
    jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(t.people) = 'array' THEN t.people ELSE '[]'::jsonb END
    ) AS person(value)
GROUP BY t.user_id, person.value::text;
