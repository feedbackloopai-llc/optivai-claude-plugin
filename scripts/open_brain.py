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

    return {
        "thought_id": thought_id,
        "summary": summary,
        "type": thought_type,
        "topics": topics,
        "people": people,
        "action_items": action_items,
    }


def search(
    conn,
    query: str,
    user_id: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sort_by: str = "similarity",
) -> List[Dict[str, Any]]:
    """Semantic search across user's thoughts using vector similarity."""
    cur = conn.cursor()

    # Generate query embedding locally
    query_embedding = _generate_embedding(query)

    order_clause = "similarity DESC" if sort_by != "time" else "created_at ASC"

    search_sql = f"""
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
            1 - (embedding <=> %s::vector) AS similarity
        FROM {TABLE}
        WHERE user_id = %s
          AND embedding IS NOT NULL
        ORDER BY {order_clause}
        LIMIT %s
    """
    cur.execute(search_sql, (str(query_embedding), user_id, limit))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    results = []
    for row in rows:
        d = dict(zip(columns, row))
        sim = d.get("similarity", 0)
        if sim is not None and float(sim) >= threshold:
            d["created_at"] = str(d["created_at"]) if d.get("created_at") else ""
            d["topics"] = _parse_array(d.get("topics"))
            d["people"] = _parse_array(d.get("people"))
            d["action_items"] = _parse_array(d.get("action_items"))
            d["similarity"] = round(float(sim), 4)
            # Normalize to uppercase keys for compatibility with formatters/Pi bridge
            d = {k.upper(): v for k, v in d.items()}
            results.append(d)

    return results


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
        sim = r.get("SIMILARITY", 0)
        summary = r.get("SUMMARY") or r.get("RAW_TEXT", "")[:100]
        if sort_by == "time":
            date = r.get("CREATED_AT", "")[:19]
            lines.append(f"{i}. [{date}] {summary}")
            lines.append(f"   Type: {r.get('THOUGHT_TYPE', '?')}  |  {sim:.0%} match")
        else:
            lines.append(f"{i}. [{sim:.0%} match] {summary}")
            lines.append(f"   Type: {r.get('THOUGHT_TYPE', '?')}  |  {r.get('CREATED_AT', '')[:19]}")
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
            )
            print(json.dumps(results, default=str))
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
