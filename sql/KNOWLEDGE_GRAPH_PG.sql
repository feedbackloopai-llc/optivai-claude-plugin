-- Knowledge Graph Schema: PostgreSQL adaptation for OptivAI Brain
-- Target: Neon PostgreSQL (free tier compatible)
--
-- Adapted from GrowthZone Snowflake KNOWLEDGE_SCHEMA.sql.
-- Lives alongside brain.thoughts in the same schema.
--
-- Tables:
--   brain.knowledge_graph_nodes  — Graph nodes (topics, people, projects, thoughts)
--   brain.knowledge_graph_edges  — Graph edges (relationships between nodes)
--
-- Usage:
--   Execute in psql or Neon SQL Editor against the OptivAI database.
--   Requires: pgcrypto or PG >= 14 (for gen_random_uuid).

-- =============================================================================
-- Prerequisites
-- =============================================================================

-- gen_random_uuid() is built-in since PG 13. On older versions uncomment:
-- CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- pgvector should already exist from BRAIN_SCHEMA_PG.sql, but ensure it:
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS brain;

SET search_path TO brain;

-- =============================================================================
-- KNOWLEDGE_GRAPH_NODES
-- One row per distinct entity in the knowledge graph.
-- Upserted by (node_nk, user_id) — idempotent.
-- node_id is auto-generated UUID cast to text for FK compatibility.
--
-- Node types: thought, topic, person, project, concept, pattern, decision
-- =============================================================================
CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
    node_id             VARCHAR(64)       NOT NULL DEFAULT gen_random_uuid()::text,
    node_nk             VARCHAR(500)      NOT NULL,
    node_type           VARCHAR(50)       NOT NULL,
    name                VARCHAR(500)      NOT NULL,
    definition          VARCHAR(4000),
    user_id             VARCHAR(100)      NOT NULL,
    source_thought_id   VARCHAR(64),
    lifecycle_status    VARCHAR(20)       NOT NULL DEFAULT 'active',
    created_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_kg_nodes PRIMARY KEY (node_id),
    CONSTRAINT uq_kg_nodes_nk UNIQUE (node_nk, user_id),
    CONSTRAINT fk_kg_nodes_thought FOREIGN KEY (source_thought_id)
        REFERENCES brain.thoughts(thought_id) ON DELETE SET NULL,
    CONSTRAINT chk_kg_nodes_type CHECK (
        node_type IN ('thought', 'topic', 'person', 'project', 'concept', 'pattern', 'decision')
    ),
    CONSTRAINT chk_kg_nodes_status CHECK (
        lifecycle_status IN ('active', 'retired', 'merged')
    )
);

COMMENT ON TABLE knowledge_graph_nodes IS
    'Knowledge graph nodes: topic, person, project, concept, pattern, decision. Derived from brain.thoughts.';

-- =============================================================================
-- KNOWLEDGE_GRAPH_EDGES
-- One row per directional relationship between two nodes.
-- Upserted by (source_node, target_node, edge_type, user_id) — idempotent.
--
-- Edge types: TAGGED_WITH, MENTIONED_BY, RELATED_TO_PROJECT, CO_OCCURS,
--             DERIVES_FROM, DEPENDS_ON, SIMILAR_TO
-- =============================================================================
CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
    edge_id             VARCHAR(128)      NOT NULL,
    source_node         VARCHAR(64)       NOT NULL,
    target_node         VARCHAR(64)       NOT NULL,
    edge_type           VARCHAR(50)       NOT NULL,
    weight              FLOAT             NOT NULL DEFAULT 1.0,
    user_id             VARCHAR(100)      NOT NULL,
    created_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_kg_edges PRIMARY KEY (edge_id),
    CONSTRAINT uq_kg_edges UNIQUE (source_node, target_node, edge_type, user_id),
    CONSTRAINT fk_kg_edges_source FOREIGN KEY (source_node)
        REFERENCES brain.knowledge_graph_nodes(node_id) ON DELETE CASCADE,
    CONSTRAINT fk_kg_edges_target FOREIGN KEY (target_node)
        REFERENCES brain.knowledge_graph_nodes(node_id) ON DELETE CASCADE,
    CONSTRAINT chk_kg_edges_type CHECK (
        edge_type IN ('TAGGED_WITH', 'MENTIONED_BY', 'RELATED_TO_PROJECT',
                      'CO_OCCURS', 'DERIVES_FROM', 'DEPENDS_ON', 'SIMILAR_TO')
    ),
    CONSTRAINT chk_kg_edges_weight CHECK (weight >= 0.0 AND weight <= 100.0),
    CONSTRAINT chk_kg_edges_no_self CHECK (source_node <> target_node)
);

COMMENT ON TABLE knowledge_graph_edges IS
    'Knowledge graph edges: TAGGED_WITH, MENTIONED_BY, RELATED_TO_PROJECT, CO_OCCURS, DERIVES_FROM, DEPENDS_ON, SIMILAR_TO.';

-- =============================================================================
-- Indexes for efficient graph traversal
-- =============================================================================

-- Node lookups
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type_user
    ON knowledge_graph_nodes (user_id, node_type);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_nk
    ON knowledge_graph_nodes (node_nk);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_status
    ON knowledge_graph_nodes (user_id, lifecycle_status)
    WHERE lifecycle_status = 'active';

CREATE INDEX IF NOT EXISTS idx_kg_nodes_source_thought
    ON knowledge_graph_nodes (source_thought_id)
    WHERE source_thought_id IS NOT NULL;

-- Edge traversal (outbound from a node)
CREATE INDEX IF NOT EXISTS idx_kg_edges_source
    ON knowledge_graph_edges (source_node, edge_type);

-- Edge traversal (inbound to a node)
CREATE INDEX IF NOT EXISTS idx_kg_edges_target
    ON knowledge_graph_edges (target_node, edge_type);

-- Edge filtering by type and user
CREATE INDEX IF NOT EXISTS idx_kg_edges_user_type
    ON knowledge_graph_edges (user_id, edge_type);

-- =============================================================================
-- Updated-at trigger: auto-set updated_at on node modification
-- =============================================================================
CREATE OR REPLACE FUNCTION brain.kg_nodes_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kg_nodes_updated_at ON brain.knowledge_graph_nodes;

CREATE TRIGGER trg_kg_nodes_updated_at
    BEFORE UPDATE ON brain.knowledge_graph_nodes
    FOR EACH ROW
    EXECUTE FUNCTION brain.kg_nodes_set_updated_at();

-- =============================================================================
-- View: Active knowledge graph (excludes retired/merged nodes and their edges)
-- =============================================================================
CREATE OR REPLACE VIEW brain.v_knowledge_graph_active AS
SELECT
    e.edge_id,
    e.edge_type,
    e.weight,
    e.user_id,
    s.node_id   AS source_id,
    s.node_nk   AS source_nk,
    s.node_type  AS source_type,
    s.name       AS source_name,
    t.node_id   AS target_id,
    t.node_nk   AS target_nk,
    t.node_type  AS target_type,
    t.name       AS target_name
FROM brain.knowledge_graph_edges e
JOIN brain.knowledge_graph_nodes s
    ON e.source_node = s.node_id AND s.lifecycle_status = 'active'
JOIN brain.knowledge_graph_nodes t
    ON e.target_node = t.node_id AND t.lifecycle_status = 'active';

COMMENT ON VIEW brain.v_knowledge_graph_active IS
    'Active edges with resolved node names — excludes retired and merged nodes.';

-- =============================================================================
-- View: Node degree summary (how connected each node is)
-- =============================================================================
CREATE OR REPLACE VIEW brain.v_knowledge_node_degrees AS
SELECT
    n.node_id,
    n.node_nk,
    n.node_type,
    n.name,
    n.user_id,
    COALESCE(out_deg.cnt, 0) AS out_degree,
    COALESCE(in_deg.cnt, 0)  AS in_degree,
    COALESCE(out_deg.cnt, 0) + COALESCE(in_deg.cnt, 0) AS total_degree
FROM brain.knowledge_graph_nodes n
LEFT JOIN (
    SELECT source_node, COUNT(*) AS cnt
    FROM brain.knowledge_graph_edges
    GROUP BY source_node
) out_deg ON n.node_id = out_deg.source_node
LEFT JOIN (
    SELECT target_node, COUNT(*) AS cnt
    FROM brain.knowledge_graph_edges
    GROUP BY target_node
) in_deg ON n.node_id = in_deg.target_node
WHERE n.lifecycle_status = 'active';

COMMENT ON VIEW brain.v_knowledge_node_degrees IS
    'Degree centrality per active node: in-degree, out-degree, total degree.';

-- =============================================================================
-- Recursive CTE: Graph traversal (N-hop neighborhood)
--
-- Usage — find all nodes within N hops of a given node:
--
--   WITH RECURSIVE graph_walk AS (
--       -- Seed: direct neighbors of the starting node
--       SELECT
--           e.target_node AS node_id,
--           1 AS depth,
--           ARRAY[e.source_node, e.target_node] AS path
--       FROM brain.knowledge_graph_edges e
--       WHERE e.source_node = '<start_node_id>'
--         AND e.user_id = '<user_id>'
--
--       UNION ALL
--
--       -- Recurse: follow outbound edges, prevent cycles via path check
--       SELECT
--           e.target_node,
--           gw.depth + 1,
--           gw.path || e.target_node
--       FROM brain.knowledge_graph_edges e
--       JOIN graph_walk gw ON e.source_node = gw.node_id
--       WHERE gw.depth < 3                          -- max hops
--         AND e.target_node <> ALL(gw.path)          -- cycle prevention
--         AND e.user_id = '<user_id>'
--   )
--   SELECT DISTINCT ON (node_id)
--       gw.node_id,
--       gw.depth AS min_depth,
--       n.node_nk,
--       n.name,
--       n.node_type
--   FROM graph_walk gw
--   JOIN brain.knowledge_graph_nodes n ON gw.node_id = n.node_id
--   ORDER BY node_id, depth;
--
-- =============================================================================

-- =============================================================================
-- Helper function: N-hop neighborhood as a set-returning function
-- Returns all nodes reachable within max_hops from a starting node.
-- =============================================================================
CREATE OR REPLACE FUNCTION brain.kg_neighborhood(
    p_start_node VARCHAR(64),
    p_user_id    VARCHAR(100),
    p_max_hops   INT DEFAULT 2
)
RETURNS TABLE (
    node_id    VARCHAR(64),
    node_nk    VARCHAR(500),
    node_type  VARCHAR(50),
    name       VARCHAR(500),
    min_depth  INT
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE graph_walk AS (
        SELECT
            e.target_node AS walked_node_id,
            1 AS depth,
            ARRAY[e.source_node, e.target_node]::varchar[] AS path
        FROM brain.knowledge_graph_edges e
        WHERE e.source_node = p_start_node
          AND e.user_id = p_user_id

        UNION ALL

        SELECT
            e.target_node,
            gw.depth + 1,
            gw.path || e.target_node
        FROM brain.knowledge_graph_edges e
        JOIN graph_walk gw ON e.source_node = gw.walked_node_id
        WHERE gw.depth < p_max_hops
          AND e.target_node <> ALL(gw.path)
          AND e.user_id = p_user_id
    )
    SELECT DISTINCT ON (gw.walked_node_id)
        gw.walked_node_id,
        n.node_nk,
        n.node_type,
        n.name,
        gw.depth
    FROM graph_walk gw
    JOIN brain.knowledge_graph_nodes n ON gw.walked_node_id = n.node_id
    WHERE n.lifecycle_status = 'active'
    ORDER BY gw.walked_node_id, gw.depth;
$$;

COMMENT ON FUNCTION brain.kg_neighborhood IS
    'Returns all active nodes reachable within N hops of a starting node. Cycle-safe.';

-- =============================================================================
-- Helper function: Shortest path between two nodes (BFS)
-- Returns the path as an array of node_ids, or empty if unreachable.
-- =============================================================================
CREATE OR REPLACE FUNCTION brain.kg_shortest_path(
    p_start_node VARCHAR(64),
    p_end_node   VARCHAR(64),
    p_user_id    VARCHAR(100),
    p_max_hops   INT DEFAULT 5
)
RETURNS TABLE (
    path       VARCHAR(64)[],
    hop_count  INT
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE bfs AS (
        SELECT
            e.target_node AS current_node,
            ARRAY[e.source_node, e.target_node]::varchar[] AS path,
            1 AS depth
        FROM brain.knowledge_graph_edges e
        WHERE e.source_node = p_start_node
          AND e.user_id = p_user_id

        UNION ALL

        SELECT
            e.target_node,
            b.path || e.target_node,
            b.depth + 1
        FROM brain.knowledge_graph_edges e
        JOIN bfs b ON e.source_node = b.current_node
        WHERE b.depth < p_max_hops
          AND e.target_node <> ALL(b.path)
          AND e.user_id = p_user_id
    )
    SELECT b.path, b.depth
    FROM bfs b
    WHERE b.current_node = p_end_node
    ORDER BY b.depth
    LIMIT 1;
$$;

COMMENT ON FUNCTION brain.kg_shortest_path IS
    'BFS shortest path between two nodes. Returns path array and hop count. Cycle-safe.';
