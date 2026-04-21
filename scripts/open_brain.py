#!/usr/bin/env python3
"""
Persistent Memory: User-scoped semantic knowledge system backed by PostgreSQL + pgvector.

Captures and recalls thoughts with vector embeddings for semantic search.
Embeddings: local all-mpnet-base-v2 via sentence-transformers (768-dim).
Metadata extraction: Claude API (claude-3-5-haiku-latest).

Usage:
    python3 open_brain.py --init                    # Create schema + table
    python3 open_brain.py --capture "your thought"  # Commit to memory
    python3 open_brain.py --search "query"          # Recall by meaning
    python3 open_brain.py --search "query" --sort time  # Oldest first (evolution)
    python3 open_brain.py --timeline "topic"        # Topic evolution over time
    python3 open_brain.py --recent                  # Recent memories
    python3 open_brain.py --recent --days 7         # Last 7 days
    python3 open_brain.py --stats                   # Memory distribution
    python3 open_brain.py --from-pi                 # Pi bridge (stdin JSON)

All operations are user-scoped. USER_ID derived from $USER env var.
"""

import os
import sys
import json
import time
import hashlib
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("open_brain")

# ─── Configuration ────────────────────────────────────────────────────────────

EMBED_MODEL = "all-mpnet-base-v2"
LLM_MODEL = "claude-haiku-4-5-20251001"
SCHEMA = "brain"
TABLE = f"{SCHEMA}.thoughts"
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_SIMILARITY_THRESHOLD = 0.3
DEFAULT_RECENT_DAYS = 7
DEFAULT_RECENT_LIMIT = 20

METADATA_EXTRACTION_PROMPT = '''Extract metadata from this thought. Return ONLY valid JSON, no markdown fencing, no explanation:
{{
  "type": "decision|insight|person_note|meeting|idea|task|reflection|preference|impression|pattern|working_memory",
  "topics": ["topic1", "topic2"],
  "people": ["Name1"],
  "action_items": ["action1"],
  "summary": "one sentence summary",
  "confidence": "high|medium|low",
  "scope": "session|project|permanent"
}}

Types:
- decision: A choice made with reasoning (what was chosen and why)
- insight: A realization or discovery about how something works
- person_note: Context about a specific person (role, preferences, interactions)
- meeting: Meeting outcomes, attendees, decisions made
- idea: A concept or possibility worth remembering
- task: A work item or deliverable to track
- reflection: What went well/poorly, lessons learned from experience
- preference: A user preference, style choice, or "always/never do X" rule
- impression: A formed opinion about a person, system, or process
- pattern: A recurring approach, technique, or gotcha that applies broadly
- working_memory: Active context from current work that should bridge sessions

Scope: "session" for temporary context, "project" for project-specific, "permanent" for universal knowledge.
Confidence: How certain is this — "high" for stated facts, "medium" for inferences, "low" for hunches.

If a field has no entries, use an empty array [] or null. Always include all 8 fields.
Pick exactly ONE type from the list. For topics, extract 1-5 short tags.

Thought: "{thought_text}"'''


# ─── Embedding Model (lazy-loaded singleton) ─────────────────────────────────

_embed_model = None

def _get_embedding_model():
    """Lazy-load sentence-transformers model (one-time ~420MB download)."""
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def _generate_embedding(text: str) -> list:
    """Generate 768-dim embedding using local all-mpnet-base-v2."""
    model = _get_embedding_model()
    return model.encode(text[:8000]).tolist()


# ─── Metadata Extraction (fallback chain) ────────────────────────────────────

OLLAMA_MODEL = "llama3.1:latest"
OLLAMA_URL = "http://localhost:11434"
OPENAI_MODEL = "gpt-4o-mini"


def _extract_metadata_via_claude(text: str) -> dict:
    """Extract structured metadata using Claude API (primary)."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        safe_text = text[:4000]
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=safe_text)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        raw = _strip_markdown_fencing(raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"Claude API failed ({e}), trying Ollama...")
        return None


def _ensure_ollama_ready() -> bool:
    """Ensure Ollama is installed, running, and has the required model."""
    import subprocess
    import urllib.request

    # Check if Ollama is installed
    if not any(
        (Path(p) / "ollama").exists()
        for p in os.environ.get("PATH", "").split(":")
    ):
        logger.info("Installing Ollama...")
        try:
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                check=True, capture_output=True, timeout=120,
            )
        except Exception as e:
            logger.warning(f"Ollama install failed: {e}")
            return False

    # Check if Ollama is running
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
    except Exception:
        logger.info("Starting Ollama...")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            import time
            for _ in range(10):
                time.sleep(1)
                try:
                    urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
                    break
                except Exception:
                    continue
            else:
                logger.warning("Ollama failed to start within 10s")
                return False
        except Exception as e:
            logger.warning(f"Failed to start Ollama: {e}")
            return False

    # Check if model is available, pull if not
    try:
        resp = urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
        tags = json.loads(resp.read().decode("utf-8"))
        model_names = [m["name"] for m in tags.get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        if not any(model_base in n for n in model_names):
            logger.info(f"Pulling Ollama model {OLLAMA_MODEL} (one-time download)...")
            subprocess.run(
                ["ollama", "pull", OLLAMA_MODEL],
                check=True, timeout=600,
            )
    except Exception as e:
        logger.warning(f"Ollama model check/pull failed: {e}")
        return False

    return True


def _extract_metadata_via_ollama(text: str) -> dict:
    """Extract structured metadata using local Ollama (fallback 1)."""
    try:
        if not _ensure_ollama_ready():
            return None
        import urllib.request
        safe_text = text[:4000]
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=safe_text)
        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 300},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        raw = result.get("response", "{}")
        raw = _strip_markdown_fencing(raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"Ollama failed ({e}), trying OpenAI...")
        return None


def _extract_metadata_via_openai(text: str) -> dict:
    """Extract structured metadata using OpenAI API (fallback 2)."""
    try:
        import urllib.request
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        safe_text = text[:4000]
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=safe_text)
        payload = json.dumps({
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 256,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        raw = result["choices"][0]["message"]["content"]
        raw = _strip_markdown_fencing(raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"OpenAI failed ({e}), using defaults")
        return None


def _extract_metadata(text: str) -> dict:
    """Extract metadata with fallback chain: Claude → Ollama → OpenAI → defaults."""
    result = _extract_metadata_via_claude(text)
    if result is not None:
        return result
    result = _extract_metadata_via_ollama(text)
    if result is not None:
        return result
    result = _extract_metadata_via_openai(text)
    if result is not None:
        return result
    return {}


# ─── PostgreSQL Connection ───────────────────────────────────────────────────

def _get_database_url() -> str:
    """Get PostgreSQL connection string from env or config."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        pg = config.get("destinations", {}).get("postgresql", {})
        if pg.get("connection_string"):
            return pg["connection_string"]
    raise RuntimeError(
        "No DATABASE_URL env var and no postgresql.connection_string in auto-logger-config.json"
    )


def _connect():
    """Establish PostgreSQL connection.

    Note: pgvector types are passed as string literals with ::vector casts in SQL,
    so no special type registration is needed. This works through Neon's connection pooler.
    """
    import psycopg2
    conn = psycopg2.connect(_get_database_url())
    conn.autocommit = False
    return conn


def _get_user_id() -> str:
    """Get current user ID from environment (lowercase for consistency)."""
    for var in ("USER", "USERNAME", "LOGNAME"):
        val = os.environ.get(var)
        if val:
            return val.lower()
    return "unknown"


def _generate_thought_id() -> str:
    """Generate collision-safe thought ID: brain-{epoch}-{8hex}."""
    epoch = int(time.time())
    rand = hashlib.md5(os.urandom(16)).hexdigest()[:8]
    return f"brain-{epoch}-{rand}"


def _parse_array(val) -> list:
    """Parse JSONB array column which may come back as string, list, or None."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return []
    return list(val) if hasattr(val, '__iter__') else []


def _strip_markdown_fencing(text: str) -> str:
    """Strip ```json ... ``` fencing that LLMs sometimes add."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


# ─── Core Operations ─────────────────────────────────────────────────────────

def init_schema(conn) -> str:
    """Create the brain schema and thoughts table if they don't exist."""
    ddl_path = Path(__file__).parent.parent / "sql" / "BRAIN_SCHEMA_PG.sql"
    if not ddl_path.exists():
        ddl_path = Path.home() / ".claude" / "sql" / "BRAIN_SCHEMA_PG.sql"
    if not ddl_path.exists():
        return "DDL file not found (checked repo and ~/.claude/sql/)"

    cur = conn.cursor()
    ddl = ddl_path.read_text(encoding="utf-8")

    try:
        cur.execute(ddl)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"DDL warning: {e}")
        return f"Schema initialization had warnings: {e}"

    cur.close()
    return "Schema initialized successfully"


def capture(
    conn,
    text: str,
    user_id: str,
    source: str = "manual",
    session_id: str = "",
    project: str = "",
) -> Dict[str, Any]:
    """Capture a thought with local embedding + Claude metadata extraction."""
    thought_id = _generate_thought_id()
    cur = conn.cursor()

    # Step 1: Extract metadata via Claude API
    metadata = _extract_metadata(text)
    if not metadata:
        metadata = {
            "type": "insight",
            "topics": [],
            "people": [],
            "action_items": [],
            "summary": text[:200],
        }

    thought_type = metadata.get("type", "insight")
    topics = metadata.get("topics", [])
    people = metadata.get("people", [])
    action_items = metadata.get("action_items", [])
    summary = metadata.get("summary", text[:200])

    # Step 2: Generate embedding locally
    embedding = _generate_embedding(text)

    # Step 3: INSERT
    insert_sql = """
        INSERT INTO brain.thoughts (
            thought_id, user_id, raw_text, summary, thought_type,
            topics, people, action_items, source, session_id, project,
            embedding, metadata, created_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb,
            %s, %s, %s,
            %s::vector, %s::jsonb,
            NOW(), NOW()
        )
    """
    cur.execute(
        insert_sql,
        (
            thought_id,
            user_id,
            text[:16384],
            summary[:1000],
            thought_type,
            json.dumps(topics),
            json.dumps(people),
            json.dumps(action_items),
            source,
            session_id,
            project,
            str(embedding),
            json.dumps(metadata),
        ),
    )
    conn.commit()
    cur.close()

    # Incrementally update knowledge graph with extracted metadata
    _update_graph_incremental(
        conn, thought_id, summary, thought_type, topics, people, project, user_id
    )

    return {
        "thought_id": thought_id,
        "summary": summary,
        "type": thought_type,
        "topics": topics,
        "people": people,
        "action_items": action_items,
    }


def _update_graph_incremental(
    conn,
    thought_id: str,
    summary: str,
    thought_type: str,
    topics: List[str],
    people: List[str],
    project: str,
    user_id: str,
) -> None:
    """Populate knowledge graph nodes and edges after a thought is captured.

    Creates/upserts nodes for the thought, its topics, people, and project,
    then wires edges between them. Failures are logged but never raised —
    graph update must not block the capture path.
    """
    try:
        cur = conn.cursor()

        # ── 1. Thought node ──────────────────────────────────────────────
        thought_nk = f"thought:{thought_id}"
        cur.execute(
            """
            INSERT INTO brain.knowledge_graph_nodes
                   (node_nk, node_type, name, user_id, source_thought_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
            RETURNING node_id
            """,
            (thought_nk, "thought", summary[:200], user_id, thought_id),
        )
        thought_node_id = cur.fetchone()[0]

        # ── 2. Topic nodes + TAGGED_WITH edges ──────────────────────────
        for topic in (topics or []):
            topic_lower = topic.lower().replace(" ", "_")
            topic_nk = f"topic:{topic_lower}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_nodes
                       (node_nk, node_type, name, user_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
                RETURNING node_id
                """,
                (topic_nk, "topic", topic, user_id),
            )
            topic_node_id = cur.fetchone()[0]

            edge_id = f"{user_id}|thought:{thought_id}|TAGGED_WITH|topic:{topic_lower}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_edges
                       (edge_id, source_node, target_node, edge_type, user_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (edge_id) DO NOTHING
                """,
                (edge_id, thought_node_id, topic_node_id, "TAGGED_WITH", user_id),
            )

        # ── 3. Person nodes + MENTIONED_BY edges ────────────────────────
        for person in (people or []):
            person_lower = person.lower().replace(" ", "_")
            person_nk = f"person:{person_lower}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_nodes
                       (node_nk, node_type, name, user_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
                RETURNING node_id
                """,
                (person_nk, "person", person, user_id),
            )
            person_node_id = cur.fetchone()[0]

            edge_id = f"{user_id}|person:{person_lower}|MENTIONED_BY|thought:{thought_id}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_edges
                       (edge_id, source_node, target_node, edge_type, user_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (edge_id) DO NOTHING
                """,
                (edge_id, person_node_id, thought_node_id, "MENTIONED_BY", user_id),
            )

        # ── 4. Project node + RELATED_TO_PROJECT edge ───────────────────
        if project:
            project_lower = project.lower()
            project_nk = f"project:{project_lower}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_nodes
                       (node_nk, node_type, name, user_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (node_nk, user_id) DO UPDATE SET updated_at = NOW()
                RETURNING node_id
                """,
                (project_nk, "project", project, user_id),
            )
            project_node_id = cur.fetchone()[0]

            edge_id = f"{user_id}|thought:{thought_id}|RELATED_TO_PROJECT|project:{project_lower}"
            cur.execute(
                """
                INSERT INTO brain.knowledge_graph_edges
                       (edge_id, source_node, target_node, edge_type, user_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (edge_id) DO NOTHING
                """,
                (edge_id, thought_node_id, project_node_id, "RELATED_TO_PROJECT", user_id),
            )

        conn.commit()
        cur.close()

    except Exception as e:
        logger.warning(f"Knowledge graph update failed for thought {thought_id}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "with", "by", "and", "or", "but", "not", "this", "that",
    "it", "i", "we", "they", "my", "our",
})


def _extract_keywords(query: str, max_keywords: int = 5) -> List[str]:
    """Extract meaningful keywords from a query string.

    Splits the query into words, removes stop words and short tokens,
    and returns up to max_keywords unique lowercased keywords.
    """
    words = query.lower().split()
    seen: set = set()
    keywords: List[str] = []
    for word in words:
        # Strip punctuation from edges
        cleaned = word.strip(".,!?;:\"'()[]{}")
        if not cleaned or len(cleaned) < 2:
            continue
        if cleaned in STOP_WORDS:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            keywords.append(cleaned)
        if len(keywords) >= max_keywords:
            break
    return keywords


def search(
    conn,
    query: str,
    user_id: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sort_by: str = "similarity",
    thought_type: Optional[str] = None,
    topics: Optional[List[str]] = None,
    people: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Hybrid search across user's thoughts using vector similarity, keyword boost, and time decay.

    Combines three scoring signals:
      - vec_similarity (weight 0.85): pgvector cosine similarity
      - keyword_boost (weight 0.10): fraction of query keywords found in raw_text/summary
      - time_decay (weight 0.05): recency bonus decaying to 0 over 90 days

    Supports metadata filters: thought_type, topics (OR), people (OR), date_from, date_to.
    All new parameters are Optional with None defaults for backward compatibility.
    """
    cur = conn.cursor()

    # Generate query embedding locally
    query_embedding = _generate_embedding(query)

    # Extract keywords for keyword boost scoring
    keywords = _extract_keywords(query)

    # Build keyword boost SQL expression
    if keywords:
        keyword_cases = []
        keyword_params: List[str] = []
        for kw in keywords:
            keyword_cases.append(
                "CASE WHEN raw_text ILIKE %s OR summary ILIKE %s THEN 1 ELSE 0 END"
            )
            pattern = f"%{kw}%"
            keyword_params.extend([pattern, pattern])
        keyword_boost_expr = f"({' + '.join(keyword_cases)}) / {float(len(keywords))}"
    else:
        keyword_boost_expr = "0.0"
        keyword_params = []

    # Build dynamic WHERE clauses and params
    where_clauses = ["user_id = %s", "embedding IS NOT NULL"]
    where_params: List[Any] = [user_id]

    if thought_type is not None:
        where_clauses.append("thought_type = %s")
        where_params.append(thought_type)

    if topics is not None and len(topics) > 0:
        # JSONB array containment: match if ANY topic is in the array
        topic_conditions = ["topics @> %s::jsonb" for _ in topics]
        where_clauses.append("(" + " OR ".join(topic_conditions) + ")")
        where_params.extend([json.dumps([t]) for t in topics])

    if people is not None and len(people) > 0:
        people_conditions = ["people @> %s::jsonb" for _ in people]
        where_clauses.append("(" + " OR ".join(people_conditions) + ")")
        where_params.extend([json.dumps([p]) for p in people])

    if date_from is not None:
        where_clauses.append("created_at >= %s::date")
        where_params.append(date_from)

    if date_to is not None:
        where_clauses.append("created_at <= (%s::date + INTERVAL '1 day')")
        where_params.append(date_to)

    where_sql = " AND ".join(where_clauses)

    order_clause = "hybrid_score DESC" if sort_by != "time" else "created_at ASC"

    search_sql = f"""
        WITH scored AS (
            SELECT
                thought_id,
                raw_text,
                summary,
                thought_type,
                topics,
                people,
                action_items,
                source,
                project,
                created_at,
                1 - (embedding <=> %s::vector) AS vec_similarity,
                {keyword_boost_expr} AS keyword_boost,
                GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (NOW() - GREATEST(created_at, COALESCE(updated_at, created_at)))) / (90 * 86400.0)) AS time_decay
            FROM {TABLE}
            WHERE {where_sql}
        )
        SELECT *,
            (vec_similarity * 0.85) + (keyword_boost * 0.10) + (time_decay * 0.05) AS hybrid_score
        FROM scored
        ORDER BY {order_clause}
        LIMIT %s
    """

    # Assemble all params in order: embedding, keyword patterns, where params, limit
    params: list = [str(query_embedding)]
    params.extend(keyword_params)
    params.extend(where_params)
    params.append(limit)

    cur.execute(search_sql, params)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    results = []
    for row in rows:
        d = dict(zip(columns, row))
        hybrid = d.get("hybrid_score", 0)
        vec_sim = d.get("vec_similarity", 0)
        if vec_sim is not None and float(vec_sim) >= threshold:
            d["created_at"] = str(d["created_at"]) if d.get("created_at") else ""
            d["topics"] = _parse_array(d.get("topics"))
            d["people"] = _parse_array(d.get("people"))
            d["action_items"] = _parse_array(d.get("action_items"))
            d["similarity"] = round(float(vec_sim), 4)
            d["hybrid_score"] = round(float(hybrid), 4) if hybrid is not None else 0.0
            d["keyword_boost"] = round(float(d.get("keyword_boost", 0)), 4)
            d["time_decay"] = round(float(d.get("time_decay", 0)), 4)
            # Remove intermediate columns not needed in output
            d.pop("vec_similarity", None)
            # Normalize to uppercase keys for compatibility with formatters/Pi bridge
            d = {k.upper(): v for k, v in d.items()}
            results.append(d)

    # ── Memory reinforcement: touch updated_at on accessed thoughts ──
    # This resets the time_decay clock, making frequently-accessed memories
    # stay "fresh" longer. The more you recall a memory, the more it persists.
    if results:
        try:
            accessed_ids = [r["THOUGHT_ID"] for r in results]
            reinforce_cur = conn.cursor()
            placeholders = ",".join(["%s"] * len(accessed_ids))
            reinforce_cur.execute(
                f"UPDATE {TABLE} SET updated_at = NOW() WHERE thought_id IN ({placeholders}) AND user_id = %s",
                accessed_ids + [user_id],
            )
            conn.commit()
            reinforce_cur.close()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    return results


def graph_search(
    conn,
    query: str,
    user_id: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sort_by: str = "similarity",
    thought_type: Optional[str] = None,
    topics: Optional[List[str]] = None,
    people: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    graph_hops: int = 2,
    graph_weight: float = 0.15,
) -> List[Dict[str, Any]]:
    """Graph-augmented search: hybrid search seeds expanded via knowledge graph traversal.

    Algorithm:
      1. Run hybrid search with relaxed parameters to get seed results.
      2. For the top seed results, look up their graph nodes and find N-hop neighbors.
      3. Collect thought_ids from neighbor nodes of type 'thought'.
      4. Fetch those thoughts from the database.
      5. Merge with seed results, deduplicating by thought_id.
      6. Re-score: graph-discovered thoughts get a graph_weight boost.
      7. Sort by final score and return top `limit` results.

    Falls back gracefully to regular search if graph tables are empty or on any
    graph-related error.
    """
    # Step 1: Get seed results from hybrid search (fetch more, with lower threshold)
    seeds = search(
        conn,
        query=query,
        user_id=user_id,
        limit=limit * 3,
        threshold=threshold * 0.7,
        sort_by=sort_by,
        thought_type=thought_type,
        topics=topics,
        people=people,
        date_from=date_from,
        date_to=date_to,
    )

    if not seeds:
        return seeds

    # Build a lookup of seed results by thought_id for deduplication and merging
    seed_by_id: Dict[str, Dict[str, Any]] = {}
    for s in seeds:
        tid = s.get("THOUGHT_ID")
        if tid:
            seed_by_id[tid] = s

    # Step 2-3: Expand top seeds through the knowledge graph
    graph_thought_ids: Dict[str, int] = {}  # thought_id -> min_depth from any seed
    try:
        cur = conn.cursor()

        # Check if the knowledge graph tables exist and have data
        cur.execute(
            "SELECT COUNT(*) FROM brain.knowledge_graph_nodes WHERE user_id = %s LIMIT 1",
            (user_id,),
        )
        node_count = cur.fetchone()[0]
        if node_count == 0:
            cur.close()
            return seeds[:limit]

        # Collect node_ids for the top seeds (limit expansion to top 5 for performance)
        top_seed_ids = list(seed_by_id.keys())[:5]
        if not top_seed_ids:
            cur.close()
            return seeds[:limit]

        # Batch lookup: find graph node_ids for all top seed thoughts at once
        seed_node_nks = [f"thought:{tid}" for tid in top_seed_ids]
        nk_placeholders = ",".join(["%s"] * len(seed_node_nks))
        cur.execute(
            f"""SELECT node_id, node_nk
                FROM brain.knowledge_graph_nodes
                WHERE node_nk IN ({nk_placeholders})
                  AND user_id = %s
                  AND lifecycle_status = 'active'""",
            seed_node_nks + [user_id],
        )
        seed_node_rows = cur.fetchall()

        if not seed_node_rows:
            cur.close()
            return seeds[:limit]

        # For each seed node, expand neighborhood and collect thought node IDs
        for graph_node_id, _node_nk in seed_node_rows:
            cur.execute(
                "SELECT node_id, node_nk, node_type, name, min_depth "
                "FROM brain.kg_neighborhood(%s, %s, %s)",
                (graph_node_id, user_id, graph_hops),
            )
            for neighbor_row in cur.fetchall():
                n_node_id, n_node_nk, n_node_type, n_name, n_min_depth = neighbor_row

                # Extract thought_id from thought-type nodes (node_nk = "thought:<id>")
                if n_node_type == "thought" and n_node_nk and n_node_nk.startswith("thought:"):
                    connected_tid = n_node_nk[len("thought:"):]
                    if connected_tid not in graph_thought_ids or n_min_depth < graph_thought_ids[connected_tid]:
                        graph_thought_ids[connected_tid] = n_min_depth

                # Non-thought nodes may have source_thought_id linking back to a thought
                if n_node_type != "thought":
                    cur.execute(
                        "SELECT source_thought_id FROM brain.knowledge_graph_nodes "
                        "WHERE node_id = %s AND source_thought_id IS NOT NULL AND user_id = %s",
                        (n_node_id, user_id),
                    )
                    src_row = cur.fetchone()
                    if src_row and src_row[0]:
                        src_tid = src_row[0]
                        if src_tid not in graph_thought_ids or n_min_depth < graph_thought_ids[src_tid]:
                            graph_thought_ids[src_tid] = n_min_depth

        # Step 3: Identify graph-discovered thoughts NOT already in seed results
        new_thought_ids = [tid for tid in graph_thought_ids if tid not in seed_by_id]

        graph_results: List[Dict[str, Any]] = []
        if new_thought_ids:
            # Batch fetch new thoughts from the thoughts table
            id_placeholders = ",".join(["%s"] * len(new_thought_ids))

            # Apply the same metadata filters as the original search
            extra_where = ""
            extra_params: List[Any] = []
            if thought_type is not None:
                extra_where += " AND thought_type = %s"
                extra_params.append(thought_type)
            if date_from is not None:
                extra_where += " AND created_at >= %s::date"
                extra_params.append(date_from)
            if date_to is not None:
                extra_where += " AND created_at <= (%s::date + INTERVAL '1 day')"
                extra_params.append(date_to)

            fetch_sql = f"""
                SELECT
                    thought_id, raw_text, summary, thought_type,
                    topics, people, action_items, source, project, created_at
                FROM {TABLE}
                WHERE thought_id IN ({id_placeholders})
                  AND user_id = %s
                  {extra_where}
            """
            fetch_params: list = list(new_thought_ids) + [user_id] + extra_params
            cur.execute(fetch_sql, fetch_params)
            columns = [desc[0] for desc in cur.description]
            for row in cur.fetchall():
                d = dict(zip(columns, row))
                tid = d["thought_id"]
                d["created_at"] = str(d["created_at"]) if d.get("created_at") else ""
                d["topics"] = _parse_array(d.get("topics"))
                d["people"] = _parse_array(d.get("people"))
                d["action_items"] = _parse_array(d.get("action_items"))

                # Apply topic and people filters in-memory (JSONB array checks
                # are awkward to batch alongside an IN clause)
                if topics is not None and len(topics) > 0:
                    thought_topics = [t.lower() for t in d["topics"]]
                    if not any(t.lower() in thought_topics for t in topics):
                        continue
                if people is not None and len(people) > 0:
                    thought_people = [p.lower() for p in d["people"]]
                    if not any(p.lower() in thought_people for p in people):
                        continue

                # Score graph-discovered thoughts by proximity:
                # closer hops = higher contribution, scaled by graph_weight
                depth = graph_thought_ids.get(tid, graph_hops)
                proximity_score = max(0.0, 1.0 - (depth / (graph_hops + 1)))
                d["similarity"] = 0.0
                d["hybrid_score"] = round(proximity_score * graph_weight, 4)
                d["keyword_boost"] = 0.0
                d["time_decay"] = 0.0
                d["graph_depth"] = depth
                d["graph_source"] = True

                d = {k.upper(): v for k, v in d.items()}
                graph_results.append(d)

        cur.close()

        # Step 4: Merge seed results with graph-discovered results
        # Seeds that also appear in the graph get a proximity boost
        merged: List[Dict[str, Any]] = []
        for s in seeds:
            tid = s.get("THOUGHT_ID")
            if tid and tid in graph_thought_ids:
                depth = graph_thought_ids[tid]
                proximity_bonus = max(0.0, 1.0 - (depth / (graph_hops + 1))) * graph_weight
                boosted_score = s.get("HYBRID_SCORE", 0.0) + proximity_bonus
                s = dict(s)  # copy to avoid mutating the original
                s["HYBRID_SCORE"] = round(boosted_score, 4)
                s["GRAPH_DEPTH"] = depth
                s["GRAPH_SOURCE"] = False  # was already a seed, just boosted
            merged.append(s)

        # Add graph-only results
        merged.extend(graph_results)

        # Sort by final hybrid_score descending
        merged.sort(key=lambda r: r.get("HYBRID_SCORE", 0.0), reverse=True)

        # Apply threshold filter: seed results must meet similarity threshold,
        # graph-discovered results are kept (they passed proximity threshold)
        final: List[Dict[str, Any]] = []
        for r in merged:
            if r.get("GRAPH_SOURCE"):
                final.append(r)
            else:
                sim = r.get("SIMILARITY", 0.0)
                if sim >= threshold:
                    final.append(r)
            if len(final) >= limit:
                break

        return final

    except Exception as e:
        # Graph expansion failed -- fall back gracefully to seed results
        logger.warning(f"Graph search expansion failed ({e}), falling back to hybrid search")
        return seeds[:limit]


def admin_stats(conn) -> Dict[str, Any]:
    """Return admin-level statistics: total thoughts, user count, and per-user breakdown.

    This function is not user-scoped -- it returns aggregate stats across all users.
    """
    cur = conn.cursor()

    # Total thoughts
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    total_thoughts = cur.fetchone()[0]

    # Distinct users
    cur.execute(f"SELECT COUNT(DISTINCT user_id) FROM {TABLE}")
    user_count = cur.fetchone()[0]

    # Per-user breakdown
    cur.execute(f"""
        SELECT
            user_id,
            COUNT(*) AS thought_count,
            COUNT(DISTINCT thought_type) AS distinct_types,
            MIN(created_at) AS first_thought,
            MAX(created_at) AS last_thought
        FROM {TABLE}
        GROUP BY user_id
        ORDER BY thought_count DESC
    """)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    per_user = []
    for row in rows:
        d = dict(zip(columns, row))
        d["first_thought"] = str(d["first_thought"]) if d.get("first_thought") else ""
        d["last_thought"] = str(d["last_thought"]) if d.get("last_thought") else ""
        per_user.append(d)

    return {
        "total_thoughts": total_thoughts,
        "user_count": user_count,
        "per_user": per_user,
    }


def recent(
    conn,
    user_id: str,
    days: int = DEFAULT_RECENT_DAYS,
    limit: int = DEFAULT_RECENT_LIMIT,
    thought_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recent thoughts for a user."""
    cur = conn.cursor()

    where_clauses = ["user_id = %s"]
    params: list = [user_id]

    where_clauses.append(f"created_at >= NOW() - INTERVAL '{int(days)} days'")

    if thought_type:
        where_clauses.append("thought_type = %s")
        params.append(thought_type)

    params.append(int(limit))

    sql = f"""
        SELECT
            thought_id, raw_text, summary, thought_type,
            topics, people, action_items, source, project, created_at
        FROM {TABLE}
        WHERE {' AND '.join(where_clauses)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    cur.execute(sql, params)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    results = []
    for row in rows:
        d = dict(zip(columns, row))
        d["created_at"] = str(d["created_at"]) if d.get("created_at") else ""
        d["topics"] = _parse_array(d.get("topics"))
        d["people"] = _parse_array(d.get("people"))
        d["action_items"] = _parse_array(d.get("action_items"))
        d = {k.upper(): v for k, v in d.items()}
        results.append(d)

    return results


def stats(conn, user_id: str) -> Dict[str, Any]:
    """Get user's brain statistics."""
    cur = conn.cursor()

    # Basic stats
    cur.execute(
        f"SELECT * FROM {SCHEMA}.v_user_stats WHERE user_id = %s",
        (user_id,),
    )
    columns = [desc[0] for desc in cur.description]
    row = cur.fetchone()
    basic = dict(zip([c.upper() for c in columns], row)) if row else {}

    # Top topics
    cur.execute(
        f"""SELECT topic, mention_count, last_mentioned
            FROM {SCHEMA}.v_user_topics
            WHERE user_id = %s
            ORDER BY mention_count DESC LIMIT 10""",
        (user_id,),
    )
    topics = [
        {"topic": r[0], "count": r[1], "last": str(r[2])}
        for r in cur.fetchall()
    ]

    # Top people
    cur.execute(
        f"""SELECT person, mention_count, last_mentioned
            FROM {SCHEMA}.v_user_people
            WHERE user_id = %s
            ORDER BY mention_count DESC LIMIT 10""",
        (user_id,),
    )
    people = [
        {"person": r[0], "count": r[1], "last": str(r[2])}
        for r in cur.fetchall()
    ]

    # Type distribution
    cur.execute(
        f"""SELECT thought_type, COUNT(*) AS cnt
            FROM {TABLE} WHERE user_id = %s
            GROUP BY thought_type ORDER BY cnt DESC""",
        (user_id,),
    )
    types = {r[0]: r[1] for r in cur.fetchall()}

    cur.close()

    for k, v in basic.items():
        if hasattr(v, "isoformat"):
            basic[k] = str(v)

    return {
        "overview": basic,
        "top_topics": topics,
        "top_people": people,
        "type_distribution": types,
    }


DEFAULT_TIMELINE_DAYS = 90
DEFAULT_TIMELINE_LIMIT = 50


def timeline(
    conn,
    user_id: str,
    topic: str,
    days: int = DEFAULT_TIMELINE_DAYS,
    limit: int = DEFAULT_TIMELINE_LIMIT,
) -> List[Dict[str, Any]]:
    """Topic-filtered, time-ordered view of thoughts."""
    cur = conn.cursor()
    like_pattern = f"%{topic.lower()}%"

    sql = f"""
        SELECT
            thought_id, raw_text, summary, thought_type, topics, people,
            action_items, source, project, created_at
        FROM {TABLE}
        WHERE user_id = %s
          AND (topics @> to_jsonb(%s::text)
               OR LOWER(raw_text) LIKE %s
               OR LOWER(summary) LIKE %s)
          AND created_at >= NOW() - INTERVAL '{int(days)} days'
        ORDER BY created_at ASC
        LIMIT %s
    """
    cur.execute(sql, (user_id, topic.lower(), like_pattern, like_pattern, int(limit)))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    results = []
    for row in rows:
        d = dict(zip(columns, row))
        d["created_at"] = str(d["created_at"]) if d.get("created_at") else ""
        d["topics"] = _parse_array(d.get("topics"))
        d["people"] = _parse_array(d.get("people"))
        d["action_items"] = _parse_array(d.get("action_items"))
        d = {k.upper(): v for k, v in d.items()}
        results.append(d)

    return results


# ─── Formatters (human-readable CLI output) ──────────────────────────────────

def _format_capture_result(result: Dict) -> str:
    lines = [f"Captured: {result['thought_id']}"]
    lines.append(f"   Type: {result['type']}")
    lines.append(f"   Summary: {result['summary']}")
    if result.get("topics"):
        lines.append(f"   Topics: {', '.join(result['topics'])}")
    if result.get("people"):
        lines.append(f"   People: {', '.join(result['people'])}")
    if result.get("action_items"):
        lines.append(f"   Actions: {'; '.join(result['action_items'])}")
    return "\n".join(lines)


def _format_search_results(results: List[Dict], sort_by: str = "similarity") -> str:
    if not results:
        return "No matching thoughts found."
    lines = [f"Found {len(results)} matching thought(s):\n"]
    for i, r in enumerate(results, 1):
        hybrid = r.get("HYBRID_SCORE", 0)
        sim = r.get("SIMILARITY", 0)
        summary = r.get("SUMMARY") or r.get("RAW_TEXT", "")[:100]
        if sort_by == "time":
            date = r.get("CREATED_AT", "")[:19]
            lines.append(f"{i}. [{date}] {summary}")
            lines.append(f"   Type: {r.get('THOUGHT_TYPE', '?')}  |  hybrid={hybrid:.0%}  vec={sim:.0%}")
        else:
            lines.append(f"{i}. [{hybrid:.0%} hybrid] {summary}")
            lines.append(f"   Type: {r.get('THOUGHT_TYPE', '?')}  |  vec={sim:.0%}  |  {r.get('CREATED_AT', '')[:19]}")
        topics = r.get("TOPICS", [])
        if topics:
            lines.append(f"   Topics: {', '.join(str(t) for t in topics)}")
        lines.append("")
    return "\n".join(lines)


def _format_graph_search_results(results: List[Dict], sort_by: str = "similarity") -> str:
    """Format graph search results, showing graph connection info where present."""
    if not results:
        return "No matching thoughts found."

    graph_count = sum(1 for r in results if r.get("GRAPH_SOURCE"))
    boosted_count = sum(1 for r in results if r.get("GRAPH_DEPTH") is not None and not r.get("GRAPH_SOURCE"))
    header = f"Found {len(results)} thought(s)"
    if graph_count > 0 or boosted_count > 0:
        parts = []
        if graph_count > 0:
            parts.append(f"{graph_count} via graph")
        if boosted_count > 0:
            parts.append(f"{boosted_count} graph-boosted")
        header += f" ({', '.join(parts)})"
    lines = [header + ":\n"]

    for i, r in enumerate(results, 1):
        hybrid = r.get("HYBRID_SCORE", 0)
        sim = r.get("SIMILARITY", 0)
        summary = r.get("SUMMARY") or r.get("RAW_TEXT", "")[:100]
        is_graph_source = r.get("GRAPH_SOURCE", False)
        graph_depth = r.get("GRAPH_DEPTH")

        # Build the score tag
        if is_graph_source:
            score_tag = f"graph@{graph_depth}hop"
        elif graph_depth is not None:
            score_tag = f"{hybrid:.0%} hybrid+graph@{graph_depth}hop"
        elif sort_by == "time":
            score_tag = r.get("CREATED_AT", "")[:19]
        else:
            score_tag = f"{hybrid:.0%} hybrid"

        lines.append(f"{i}. [{score_tag}] {summary}")

        # Detail line
        detail_parts = [f"Type: {r.get('THOUGHT_TYPE', '?')}"]
        if not is_graph_source:
            detail_parts.append(f"vec={sim:.0%}")
        if graph_depth is not None:
            detail_parts.append(f"depth={graph_depth}")
        detail_parts.append(r.get("CREATED_AT", "")[:19])
        lines.append(f"   {'  |  '.join(detail_parts)}")

        topics = r.get("TOPICS", [])
        if topics:
            lines.append(f"   Topics: {', '.join(str(t) for t in topics)}")
        lines.append("")

    return "\n".join(lines)


def _format_timeline_results(results: List[Dict], topic: str) -> str:
    if not results:
        return f"No thoughts found for topic: {topic}"
    first_date = results[0].get("CREATED_AT", "")[:19]
    last_date = results[-1].get("CREATED_AT", "")[:19]
    span = f"{first_date} to {last_date}" if first_date != last_date else first_date
    lines = [f'Topic: "{topic}" ({len(results)} thoughts over {span})\n']
    for r in results:
        date = r.get("CREATED_AT", "")[:19]
        ttype = r.get("THOUGHT_TYPE", "?")
        summary = r.get("SUMMARY") or r.get("RAW_TEXT", "")[:100]
        lines.append(f"  {date}  ({ttype})  {summary}")
    return "\n".join(lines)


def _format_recent_results(results: List[Dict]) -> str:
    if not results:
        return "No recent thoughts."
    lines = [f"Recent thoughts ({len(results)}):\n"]
    for r in results:
        date = r.get("CREATED_AT", "")[:19]
        ttype = r.get("THOUGHT_TYPE", "?")
        summary = r.get("SUMMARY") or r.get("RAW_TEXT", "")[:100]
        lines.append(f"  [{date}] ({ttype}) {summary}")
    return "\n".join(lines)


def _format_stats(s: Dict) -> str:
    o = s.get("overview", {})
    if not o:
        return "No memories captured yet. Use --capture to start!"
    lines = [
        "Memory Stats",
        f"   Total thoughts: {o.get('TOTAL_THOUGHTS', 0)}",
        f"   This week: {o.get('THOUGHTS_THIS_WEEK', 0)}",
        f"   This month: {o.get('THOUGHTS_THIS_MONTH', 0)}",
        f"   With action items: {o.get('THOUGHTS_WITH_ACTIONS', 0)}",
        f"   Unique types: {o.get('DISTINCT_TYPES', 0)}",
    ]
    if s.get("top_topics"):
        lines.append("\n   Top Topics:")
        for t in s["top_topics"][:5]:
            lines.append(f"     {t['topic']} ({t['count']}x)")
    if s.get("top_people"):
        lines.append("\n   Top People:")
        for p in s["top_people"][:5]:
            lines.append(f"     {p['person']} ({p['count']}x)")
    if s.get("type_distribution"):
        lines.append("\n   By Type:")
        for ttype, count in s["type_distribution"].items():
            lines.append(f"     {ttype}: {count}")
    return "\n".join(lines)


# ─── Pi bridge ────────────────────────────────────────────────────────────────

def _run_from_pi():
    """Pi bridge: read JSON from stdin, dispatch operation, print JSON result."""
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    op = args.get("op", "")
    user_id = args.get("user_id", _get_user_id())

    conn = _connect()
    try:
        if op == "capture":
            result = capture(
                conn,
                text=args.get("text", ""),
                user_id=user_id,
                source=args.get("source", "pi"),
                session_id=args.get("session_id", ""),
                project=args.get("project", ""),
            )
            print(json.dumps(result))
        elif op == "search":
            results = search(
                conn,
                query=args.get("query", ""),
                user_id=user_id,
                limit=args.get("limit", DEFAULT_SEARCH_LIMIT),
                threshold=args.get("threshold", DEFAULT_SIMILARITY_THRESHOLD),
                sort_by=args.get("sort_by", "similarity"),
                thought_type=args.get("thought_type"),
                topics=args.get("topics"),
                people=args.get("people"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
            )
            print(json.dumps(results, default=str))
        elif op == "graph_search":
            results = graph_search(
                conn,
                query=args.get("query", ""),
                user_id=user_id,
                limit=args.get("limit", DEFAULT_SEARCH_LIMIT),
                threshold=args.get("threshold", DEFAULT_SIMILARITY_THRESHOLD),
                sort_by=args.get("sort_by", "similarity"),
                thought_type=args.get("thought_type"),
                topics=args.get("topics"),
                people=args.get("people"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                graph_hops=args.get("graph_hops", 2),
                graph_weight=args.get("graph_weight", 0.15),
            )
            print(json.dumps(results, default=str))
        elif op == "admin_stats":
            result = admin_stats(conn)
            print(json.dumps(result, default=str))
        elif op == "timeline":
            results = timeline(
                conn,
                user_id=user_id,
                topic=args.get("topic", ""),
                days=args.get("days", DEFAULT_TIMELINE_DAYS),
                limit=args.get("limit", DEFAULT_TIMELINE_LIMIT),
            )
            print(json.dumps(results, default=str))
        elif op == "recent":
            results = recent(
                conn,
                user_id=user_id,
                days=args.get("days", DEFAULT_RECENT_DAYS),
                limit=args.get("limit", DEFAULT_RECENT_LIMIT),
                thought_type=args.get("thought_type"),
            )
            print(json.dumps(results, default=str))
        elif op == "stats":
            result = stats(conn, user_id=user_id)
            print(json.dumps(result, default=str))
        elif op == "init":
            result = init_schema(conn)
            print(json.dumps({"status": "ok", "message": result}))
        else:
            print(json.dumps({"error": f"Unknown op: {op}"}))
    finally:
        conn.close()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Persistent Memory: semantic knowledge system backed by PostgreSQL + pgvector"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="Initialize schema")
    group.add_argument("--capture", type=str, metavar="TEXT", help="Capture a thought")
    group.add_argument("--search", type=str, metavar="QUERY", help="Semantic search")
    group.add_argument("--recent", action="store_true", help="List recent thoughts")
    group.add_argument("--stats", action="store_true", help="Show brain stats")
    group.add_argument("--timeline", type=str, metavar="TOPIC", help="Temporal evolution of a topic")
    group.add_argument("--from-pi", action="store_true", help="Pi bridge (stdin JSON)")

    parser.add_argument("--sort", type=str, choices=["similarity", "time"], default="similarity",
                        help="Sort search results: similarity (default) or time (oldest first)")
    parser.add_argument("--days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_RECENT_LIMIT)
    parser.add_argument("--type", type=str, dest="thought_type",
                        help="Filter by type: decision|insight|person_note|meeting|idea|task|reflection|preference|impression|pattern|working_memory")
    parser.add_argument("--source", type=str, default="manual")
    parser.add_argument("--session-id", type=str, default="")
    parser.add_argument("--project", type=str, default="")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if args.from_pi:
        _run_from_pi()
        return

    user_id = _get_user_id()

    if args.init:
        conn = _connect()
        try:
            result = init_schema(conn)
            print(result)
        finally:
            conn.close()
        return

    conn = _connect()

    try:
        if args.capture:
            result = capture(
                conn,
                text=args.capture,
                user_id=user_id,
                source=args.source,
                session_id=args.session_id,
                project=args.project,
            )
            if args.json:
                print(json.dumps(result))
            else:
                print(_format_capture_result(result))

        elif args.search:
            results = search(
                conn, query=args.search, user_id=user_id, limit=args.limit,
                sort_by=args.sort,
            )
            if args.json:
                print(json.dumps(results, default=str))
            else:
                print(_format_search_results(results, sort_by=args.sort))

        elif args.timeline:
            days = args.days if args.days != DEFAULT_RECENT_DAYS else DEFAULT_TIMELINE_DAYS
            results = timeline(
                conn, user_id=user_id, topic=args.timeline,
                days=days, limit=args.limit,
            )
            if args.json:
                print(json.dumps(results, default=str))
            else:
                print(_format_timeline_results(results, args.timeline))

        elif args.recent:
            results = recent(
                conn,
                user_id=user_id,
                days=args.days,
                limit=args.limit,
                thought_type=args.thought_type,
            )
            if args.json:
                print(json.dumps(results, default=str))
            else:
                print(_format_recent_results(results))

        elif args.stats:
            result = stats(conn, user_id=user_id)
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(_format_stats(result))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
