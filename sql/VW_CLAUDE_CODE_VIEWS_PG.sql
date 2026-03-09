-- Brain views for PostgreSQL

SET search_path TO brain;

-- User stats view
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
    COUNT(*) FILTER (WHERE jsonb_array_length(action_items) > 0) AS thoughts_with_actions,
    COUNT(*) FILTER (WHERE jsonb_array_length(people) > 0) AS thoughts_with_people
FROM thoughts
GROUP BY user_id;

-- Topic frequency per user
CREATE OR REPLACE VIEW v_user_topics AS
SELECT
    t.user_id,
    topic.value::text AS topic,
    COUNT(*) AS mention_count,
    MAX(t.created_at) AS last_mentioned
FROM thoughts t,
    jsonb_array_elements_text(t.topics) AS topic(value)
GROUP BY t.user_id, topic.value::text;

-- People frequency per user
CREATE OR REPLACE VIEW v_user_people AS
SELECT
    t.user_id,
    person.value::text AS person,
    COUNT(*) AS mention_count,
    MAX(t.created_at) AS last_mentioned
FROM thoughts t,
    jsonb_array_elements_text(t.people) AS person(value)
GROUP BY t.user_id, person.value::text;
