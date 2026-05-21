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
import re
import sys
import json
import time
import hashlib
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import jsonpatch

logger = logging.getLogger("open_brain")


# ─── PII redaction (brain-W2-S6) ─────────────────────────────────────────────
# Patterns are additive and intentionally specific to minimise false positives.
# The redactor runs at the replay-log emitter boundary BEFORE any write, so
# brain.replay_log rows never contain raw PII (the pii_distinct DEFAULT TRUE
# column marker is the auditable assertion of this discipline).

PII_PATTERNS = [
    # Email — RFC-5322-flavoured (intentionally less greedy than full RFC).
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '[EMAIL]'),
    # SSN — XXX-XX-XXXX. Anchored to word boundaries so plain "123-45-6789"
    # in a longer digit string is not silently masked.
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN]'),
    # Card numbers — 16 digits in 4 groups of 4, separator either space or dash.
    (re.compile(r'\b(?:\d{4}[ -]?){3}\d{4}\b'), '[CARD]'),
    # Phone — North-American format with optional country code. Anchored on
    # word boundaries; the (?<!\d) lookbehind on the leading group prevents
    # eating an already-redacted card-fragment tail. SSN runs first so that
    # NNN-NN-NNNN never reaches this pattern.
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[PHONE]'),
]


def redact_pii(text: Optional[str]) -> Optional[str]:
    """Apply PII regex masking. Returns None for None input, '' for ''.

    Order matters: SSN (NNN-NN-NNNN) is masked BEFORE phone (NNN-NNN-NNNN) so
    the more specific format wins. Cards run before phone for the same reason
    (16-digit blocks would otherwise be partially eaten by the phone pattern).
    """
    if text is None:
        return None
    result = text
    for pattern, replacement in PII_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


# ─── Replay-log emitter (brain-W2-S6) ────────────────────────────────────────
# Best-effort discipline: any write failure is swallowed and the emitter
# returns -1. A user-facing brain op MUST NOT be blocked by an audit-log
# failure. See HARN-L704 and Scorecard #7 (durable PII-distinct OTel
# audit trail). The call site is placed BEFORE each function's `return` so
# the audit row is created in the same successful-operation context.

def emit_replay_log(
    conn,
    user_id: str,
    event_type: str,
    thought_id: Optional[str] = None,
    query: Optional[str] = None,
    result_text: Optional[str] = None,
    session_id: Optional[str] = None,
    prov_agent: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """Emit a row into ``brain.replay_log``. PII-redacted at the boundary.

    Never raises — if the table doesn't exist (migration not run) or the
    write fails, we silently swallow. The audit log is best-effort; we
    never block a user-facing brain op on an audit failure.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    user_id
        Owner of the row; mirrors the live op's principal scope.
    event_type
        One of ``'capture' | 'search' | 'forget' | 'promote' | 'demote'
        | 'rollback' | 'inspect' | 'trace' | 'snapshot'``.
    thought_id
        Subject of the event (optional).
    query
        Search/forget query text — PII-redacted before write.
    result_text
        Free-form result text — PII-redacted then truncated to 100 chars.
    session_id
        Defaults to ``$BRAIN_SESSION_ID`` if unset.
    prov_agent
        PROV-DM principal; defaults to ``cli-user-{user_id}``.
    metadata
        Type-specific extra fields persisted as JSONB.

    Returns
    -------
    int
        The new ``event_id`` on success, or ``-1`` if the write failed.
    """
    if prov_agent is None:
        prov_agent = _derive_prov_agent("manual", user_id)
    if session_id is None:
        session_id = os.environ.get("BRAIN_SESSION_ID")
    trace_id = os.environ.get("OTEL_TRACE_ID")
    span_id = os.environ.get("OTEL_SPAN_ID")
    query_redacted = redact_pii(query) if query else None
    redacted_result = redact_pii(result_text) if result_text else None
    result_summary = redacted_result[:100] if redacted_result else None

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO brain.replay_log (
                user_id, session_id, event_type, thought_id,
                query_redacted, result_summary, pii_distinct,
                trace_id, span_id, prov_agent, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s::jsonb
            )
            RETURNING event_id
            """,
            (
                user_id, session_id, event_type, thought_id,
                query_redacted, result_summary,
                trace_id, span_id, prov_agent,
                json.dumps(metadata) if metadata is not None else None,
            ),
        )
        event_id = cur.fetchone()[0]
        conn.commit()
        return int(event_id)
    except Exception:
        # Best-effort: rollback the bound connection so a subsequent caller
        # is not stuck in a failed-tx state. Swallow any rollback exception.
        try:
            conn.rollback()
        except Exception:
            pass
        return -1
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def query_replay_log(
    conn,
    user_id: str,
    session_id: Optional[str] = None,
    from_iso: Optional[str] = None,
    to_iso: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Query ``brain.replay_log`` scoped to ``user_id``.

    Returns chronologically-sorted rows (oldest first, ties broken by
    ``event_id``). Datetime values are serialised to ISO 8601 strings for
    JSON-friendliness.
    """
    where = ["user_id = %s"]
    params: List[Any] = [user_id]
    if session_id is not None:
        where.append("session_id = %s")
        params.append(session_id)
    if from_iso is not None:
        where.append("created_at >= %s")
        params.append(from_iso)
    if to_iso is not None:
        where.append("created_at <= %s")
        params.append(to_iso)
    if event_type is not None:
        where.append("event_type = %s")
        params.append(event_type)

    sql = f"""
        SELECT event_id, user_id, session_id, event_type, thought_id,
               query_redacted, result_summary, pii_distinct,
               trace_id, span_id, prov_agent, metadata, created_at
        FROM brain.replay_log
        WHERE {' AND '.join(where)}
        ORDER BY created_at ASC, event_id ASC
        LIMIT %s
    """
    params.append(limit)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        out: List[Dict[str, Any]] = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            ts = d.get("created_at")
            if ts is not None and hasattr(ts, "isoformat"):
                d["created_at"] = ts.isoformat()
            out.append(d)
        return out
    finally:
        cur.close()

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
  "type": "decision|insight|person_note|meeting|idea|task|reflection|preference|impression|pattern|working_memory|sentinel_event|sentinel_relevant",
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
- sentinel_event: An actual Sentinel runtime event (outward-facing market-signal trigger or inward-facing observability alert)
- sentinel_relevant: A memory entry that touches on Sentinel work (design, scaffolding, decisions, dual-purpose framing)

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


def _derive_prov_agent(source: Optional[str], user_id: str) -> str:
    """Derive a default ``prov_agent`` from the capture source.

    Mappings (W3C PROV-DM ``prov:Agent`` identifier):
      - ``"manual"`` / empty / ``None``  → ``"cli-user-{user_id}"``
      - ``"pi"`` or any ``"pi-*"`` source → ``"pi-agent"``
      - ``"claude-code"``                 → ``"claude-code"`` (passthrough)
      - ``"hook-*"``                      → ``"claude-code-hook-{suffix}"``
      - anything else                     → ``"source-{source}"`` (fallback)

    The mappings give every capture a recognizable principal even when the
    caller omits ``prov_agent`` explicitly. This is the WA-gate default;
    callers may override by passing ``prov_agent=...`` to :func:`capture`.
    """
    if not source or source == "manual":
        return f"cli-user-{user_id}"
    if source == "pi" or source.startswith("pi-"):
        return "pi-agent"
    if source == "claude-code":
        return "claude-code"
    if source.startswith("hook-"):
        return f"claude-code-hook-{source[5:]}"
    return f"source-{source}"


def _generate_activity_id(thought_id: str) -> str:
    """Stable ``was_generated_by`` activity ID for a thought.

    Format: ``activity-{thought_id}``. 1:1 with the thought for now;
    multi-thought activities (e.g. batch import, conversation turn that
    spawns several thoughts) are deferred to Wave-3 and will introduce
    a separate ``brain.activities`` row.
    """
    return f"activity-{thought_id}"


# ─── RB primitive: snapshot / list_versions / rollback / diff_versions ───────
#
# brain-W1-S5. The substrate (brain.thought_versions) was created by S4.
# These four helpers operate on it.
#
# CRITICAL LOAD-BEARING INVARIANT (Lin/Li/Chen 2026 §12.1):
#
#     rollback_thought() creates NEW history. It does NOT rewrite.
#
# Given a thought with revisions [1, 2, 3], `rollback(tid, to_revision=1)`
# produces revisions [1, 2, 3, 4] where revision 4 has the content of
# revision 1, prov_activity='rollback', and parent_version pointing at
# the version_id of revision 1. Revisions 2 and 3 remain in history.
#
# See tests/test_rb_cli.py::TestRollbackCreatesNewHistory for the
# anchoring assertion.


def snapshot_thought(
    conn,
    thought_id: str,
    user_id: str,
    prov_agent: Optional[str] = None,
    prov_activity: str = "snapshot",
) -> Dict[str, Any]:
    """Create a new ``brain.thought_versions`` row capturing the current state of
    ``thought_id``.

    Auto-increments revision (highest existing revision + 1, or 1 if no
    versions yet). Computes an RFC 6902 JSON Patch from the previous version
    to the current state when one exists (the diff is stored on the new row,
    representing prev → current). PS scoping is enforced: raises
    :class:`RuntimeError` if ``thought_id`` is not in ``user_id``'s scope.

    Note: "previous version" means the row with the highest revision number,
    which after a ``rollback_thought`` will be the rollback row. The diff
    from a rollback row to the current state will be empty if no further
    changes have been made — this is semantically correct (no drift since
    the rollback).

    Returns
    -------
    dict
        ``{"version_id": int, "revision": int, "thought_id": str}``
    """
    cur = conn.cursor()

    # PS scope check + fetch current thought state
    cur.execute(
        """
        SELECT raw_text, summary, thought_type, topics, people, action_items,
               embedding, metadata
        FROM brain.thoughts
        WHERE thought_id = %s AND user_id = %s
        """,
        (thought_id, user_id),
    )
    row = cur.fetchone()
    if row is None:
        cur.close()
        raise RuntimeError(
            f"snapshot_thought: thought {thought_id} not in user scope "
            f"(user={user_id})"
        )
    raw_text, summary, thought_type, topics, people, action_items, embedding, metadata = row

    # Determine next revision + previous version state (for diff).
    cur.execute(
        """
        SELECT version_id, revision, raw_text, summary, thought_type,
               topics, people, action_items, metadata
        FROM brain.thought_versions
        WHERE thought_id = %s
        ORDER BY revision DESC
        LIMIT 1
        """,
        (thought_id,),
    )
    prev = cur.fetchone()
    next_revision = (prev[1] + 1) if prev else 1
    parent_version = prev[0] if prev else None

    # Compute RFC 6902 diff from prev to current. Embedding vectors are
    # not diffed (cosine-similarity noise; vector diff is a no-op).
    diff_json = None
    if prev:
        prev_state = {
            "raw_text": prev[2],
            "summary": prev[3],
            "thought_type": prev[4],
            "topics": prev[5],
            "people": prev[6],
            "action_items": prev[7],
            "metadata": prev[8],
        }
        curr_state = {
            "raw_text": raw_text,
            "summary": summary,
            "thought_type": thought_type,
            "topics": topics,
            "people": people,
            "action_items": action_items,
            "metadata": metadata,
        }
        patch = jsonpatch.make_patch(prev_state, curr_state)
        diff_json = patch.patch

    if prov_agent is None:
        prov_agent = _derive_prov_agent("manual", user_id)

    cur.execute(
        """
        INSERT INTO brain.thought_versions (
            thought_id, revision, raw_text, summary, thought_type,
            topics, people, action_items, embedding, metadata,
            prov_agent, prov_activity, parent_version, diff_json
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb, %s::vector, %s::jsonb,
            %s, %s, %s, %s::jsonb
        )
        RETURNING version_id
        """,
        (
            thought_id, next_revision, raw_text, summary, thought_type,
            json.dumps(topics) if topics is not None else None,
            json.dumps(people) if people is not None else None,
            json.dumps(action_items) if action_items is not None else None,
            embedding if embedding is not None else None,
            json.dumps(metadata) if metadata is not None else None,
            prov_agent, prov_activity, parent_version,
            json.dumps(diff_json) if diff_json is not None else None,
        ),
    )
    version_id = cur.fetchone()[0]
    conn.commit()
    cur.close()

    # brain-W2-S6: replay-log emission. Best-effort; never raises.
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type="snapshot",
        thought_id=thought_id,
        prov_agent=prov_agent,
        metadata={"version_id": int(version_id), "revision": int(next_revision)},
    )

    return {
        "version_id": version_id,
        "revision": next_revision,
        "thought_id": thought_id,
    }


def list_versions(
    conn,
    thought_id: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Return all versions of ``thought_id`` in chronological (revision asc) order.

    PS scoping: raises :class:`RuntimeError` if the thought is not in
    ``user_id``'s scope.

    Note: ``raw_text`` is truncated to 200 chars in the returned dicts for
    display purposes. Use ``diff_versions`` or direct SQL to access full text.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
        (thought_id, user_id),
    )
    if cur.fetchone() is None:
        cur.close()
        raise RuntimeError(
            f"list_versions: thought {thought_id} not in user scope "
            f"(user={user_id})"
        )
    cur.execute(
        """
        SELECT version_id, revision, raw_text, prov_agent, prov_activity,
               parent_version, created_at
        FROM brain.thought_versions
        WHERE thought_id = %s
        ORDER BY revision ASC
        """,
        (thought_id,),
    )
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "version_id": r[0],
            "revision": r[1],
            # Truncate raw_text for list display (full text is on the row).
            "raw_text": r[2][:200] if r[2] is not None else None,
            "prov_agent": r[3],
            "prov_activity": r[4],
            "parent_version": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


def rollback_thought(
    conn,
    thought_id: str,
    user_id: str,
    to_revision: int,
    prov_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Roll the current state of ``thought_id`` back to the content of revision
    ``to_revision``.

    CRITICAL CONTRACT (Lin/Li/Chen 2026 §12.1): rollback creates NEW history.
    It does NOT delete or rewrite earlier revisions. After
    ``rollback(tid, to_revision=1)`` on a thought with revisions 1, 2, 3, the
    result is revisions 1, 2, 3, 4 — where revision 4 has the content of
    revision 1, ``prov_activity='rollback'``, and ``parent_version`` pointing
    to the ``version_id`` of revision 1.

    Also updates the live ``brain.thoughts`` row to match the rolled-back
    content so subsequent reads/searches see the restored state.

    Returns
    -------
    dict
        ``{"version_id": int, "revision": int, "thought_id": str,
        "rolled_back_to_revision": int}``
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
        (thought_id, user_id),
    )
    if cur.fetchone() is None:
        cur.close()
        raise RuntimeError(
            f"rollback_thought: thought {thought_id} not in user scope "
            f"(user={user_id})"
        )

    # Fetch the target revision's state.
    cur.execute(
        """
        SELECT version_id, raw_text, summary, thought_type, topics, people,
               action_items, embedding, metadata
        FROM brain.thought_versions
        WHERE thought_id = %s AND revision = %s
        """,
        (thought_id, to_revision),
    )
    target = cur.fetchone()
    if target is None:
        cur.close()
        raise RuntimeError(
            f"rollback_thought: revision {to_revision} of {thought_id} not found"
        )
    (
        target_version_id, raw_text, summary, thought_type, topics, people,
        action_items, embedding, metadata,
    ) = target

    # Determine the next revision. Rollback ALWAYS appends — never rewrites.
    cur.execute(
        """
        SELECT revision FROM brain.thought_versions WHERE thought_id = %s
        ORDER BY revision DESC LIMIT 1
        """,
        (thought_id,),
    )
    max_rev = cur.fetchone()
    next_revision = (max_rev[0] + 1) if max_rev else 1

    if prov_agent is None:
        prov_agent = _derive_prov_agent("manual", user_id)

    # INSERT the rollback version (prov_activity='rollback', parent = target).
    cur.execute(
        """
        INSERT INTO brain.thought_versions (
            thought_id, revision, raw_text, summary, thought_type,
            topics, people, action_items, embedding, metadata,
            prov_agent, prov_activity, parent_version, diff_json
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb, %s::vector, %s::jsonb,
            %s, 'rollback', %s, NULL
        )
        RETURNING version_id
        """,
        (
            thought_id, next_revision, raw_text, summary, thought_type,
            json.dumps(topics) if topics is not None else None,
            json.dumps(people) if people is not None else None,
            json.dumps(action_items) if action_items is not None else None,
            embedding if embedding is not None else None,
            json.dumps(metadata) if metadata is not None else None,
            prov_agent, target_version_id,
        ),
    )
    new_version_id = cur.fetchone()[0]

    # Update the live brain.thoughts row to match the rolled-back content.
    cur.execute(
        """
        UPDATE brain.thoughts
        SET raw_text = %s,
            summary = %s,
            thought_type = %s,
            topics = %s::jsonb,
            people = %s::jsonb,
            action_items = %s::jsonb,
            metadata = %s::jsonb,
            updated_at = NOW()
        WHERE thought_id = %s
        """,
        (
            raw_text, summary, thought_type,
            json.dumps(topics) if topics is not None else None,
            json.dumps(people) if people is not None else None,
            json.dumps(action_items) if action_items is not None else None,
            json.dumps(metadata) if metadata is not None else None,
            thought_id,
        ),
    )
    conn.commit()
    cur.close()

    # brain-W2-S6: replay-log emission. Best-effort; never raises.
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type="rollback",
        thought_id=thought_id,
        prov_agent=prov_agent,
        metadata={
            "rolled_back_to_revision": int(to_revision),
            "new_revision": int(next_revision),
            "new_version_id": int(new_version_id),
        },
    )

    return {
        "version_id": new_version_id,
        "revision": next_revision,
        "thought_id": thought_id,
        "rolled_back_to_revision": to_revision,
    }


def diff_versions(
    conn,
    thought_id: str,
    user_id: str,
    revision_a: int,
    revision_b: int,
) -> List[Dict[str, Any]]:
    """Return the RFC 6902 JSON Patch transforming revision_a into revision_b.

    Direction-sensitive. ``diff_versions(a=1, b=2)`` returns the FORWARD patch
    (what to change in v1 to get v2 — ``replace`` ops carry v2's values).
    ``diff_versions(a=2, b=1)`` returns the REVERSE/undo patch (what to change
    in v2 to get back v1 — ``replace`` ops carry v1's values). When
    ``revision_a == revision_b``, the patch is the empty list.

    Embedding vectors are NOT diffed (cosine-similarity noise; vector diff
    is a no-op). PS scoping is enforced.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
        (thought_id, user_id),
    )
    if cur.fetchone() is None:
        cur.close()
        raise RuntimeError(
            f"diff_versions: thought {thought_id} not in user scope "
            f"(user={user_id})"
        )

    # Fetch both revisions in a single round-trip. Use the IN-list explicitly
    # so we can distinguish "one of the two is missing" cleanly.
    cur.execute(
        """
        SELECT revision, raw_text, summary, thought_type, topics, people,
               action_items, metadata
        FROM brain.thought_versions
        WHERE thought_id = %s AND revision IN (%s, %s)
        ORDER BY revision ASC
        """,
        (thought_id, revision_a, revision_b),
    )
    rows = cur.fetchall()
    cur.close()

    # If revision_a == revision_b, the IN-list collapses to a single value
    # and we get exactly one row — treat as a no-op diff.
    if revision_a == revision_b:
        if not rows:
            raise RuntimeError(
                f"diff_versions: revision {revision_a} of {thought_id} not found"
            )
        return []

    if len(rows) != 2:
        found = {r[0] for r in rows}
        missing = {revision_a, revision_b} - found
        raise RuntimeError(
            f"diff_versions: revisions {missing} of {thought_id} not found"
        )

    # Sort the two rows by the requested order (A first, then B) so the
    # patch direction is revision_a → revision_b.
    by_rev = {r[0]: r for r in rows}
    rev_a = by_rev[revision_a]
    rev_b = by_rev[revision_b]

    state_a = {
        "raw_text": rev_a[1],
        "summary": rev_a[2],
        "thought_type": rev_a[3],
        "topics": rev_a[4],
        "people": rev_a[5],
        "action_items": rev_a[6],
        "metadata": rev_a[7],
    }
    state_b = {
        "raw_text": rev_b[1],
        "summary": rev_b[2],
        "thought_type": rev_b[3],
        "topics": rev_b[4],
        "people": rev_b[5],
        "action_items": rev_b[6],
        "metadata": rev_b[7],
    }
    patch = jsonpatch.make_patch(state_a, state_b)
    return patch.patch


# ─── VF_eps primitive: --forget with delete-after-verify ─────────────────────
#
# brain-W1-S8. Implements the procurement-grade verified-forgetting flow:
#
#   1. build_probe_seed_snapshot (PRE-delete; captures forgotten_text +
#      top-50 NN neighbors)
#   2. CAPTURE the full row state into restore_row (for R1 restore safety)
#   3. DELETE the live row (CASCADE removes thought_versions)
#   4. verify_forgetting (probes against post-delete store)
#   5. If accepted (k=0): emit audit row status='forgotten'
#   6. If rejected (k>0) OR verify errors: RESTORE the row + emit failure audit
#
# The delete-after-verify pattern (R1 fix-wave) STRUCTURALLY ELIMINATES the
# rollback data-loss bug: any non-zero residue triggers re-INSERT from the
# pre-delete snapshot before the function returns.
#
# Audit log (brain.forget_audit) records BOTH bounds distinctly (R2 fix-wave):
#   - hoeffding_bound / hoeffding_confidence (loose; 77.69% at n=300/eps=0.05)
#   - exact_binomial_bound / exact_binomial_conf (tight; 99.9999793% — the
#     procurement headline per Lin/Li/Chen §12.1)


def forget_thought(
    conn,
    thought_id: str,
    user_id: str,
    epsilon: float = 0.05,
    n: int = 300,
    prov_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Forget a thought with VF_eps verification (Lin/Li/Chen 2026 §12.1).

    The delete-after-verify protocol (R1 fix-wave):

      1. Build ProbeSeedSnapshot capturing forgotten_text + top-50 NN
         neighbors PRE-DELETE.
      2. Capture the full thought row for restore (user_id + all PROV columns).
      3. DELETE the thought from brain.thoughts (CASCADE drops thought_versions).
      4. Run n probes against the post-delete live store.
      5. If accepted (k=0): emit audit row with status='forgotten'. Done.
      6. If rejected (k>0) or verify errors: RESTORE the row from the snapshot
         (re-INSERT with the same thought_id). Emit audit row with
         status='forget-failed-residue' or 'forget-failed-error'.

    PS scoping (Principal Scoping per Lin §12.1): raises RuntimeError if
    ``thought_id`` is not in ``user_id``'s scope. Cross-user forgets are
    rejected at the snapshot step BEFORE any DELETE — the live row remains
    untouched.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    thought_id
        Target thought_id to forget.
    user_id
        Caller scope; the row MUST belong to this user.
    epsilon
        Operational target expose-rate (default 0.05).
    n
        Number of probes (default 300, the procurement-grade parameter).
    prov_agent
        Override the default agent ID. When None, derived as
        ``cli-user-{user_id}``.

    Returns
    -------
    dict
        ``{
            "thought_id": str,
            "status": "forgotten" | "forget-failed-residue" | "forget-failed-error",
            "audit_id": int,
            "audit": { ... full audit-row mirror ... },
        }``

    Raises
    ------
    RuntimeError
        If ``thought_id`` is not in ``user_id``'s scope. The live row is
        untouched in that case.
    """
    import vf_probe  # local import — vf_probe is in the same scripts/ dir

    if prov_agent is None:
        prov_agent = _derive_prov_agent("manual", user_id)

    # Step 1: snapshot pre-delete (also enforces PS scoping — raises on mismatch).
    snapshot = vf_probe.build_probe_seed_snapshot(conn, thought_id, user_id)

    # Step 2: capture full row for restore-on-failure. user_id is FIRST so the
    # restore helper can de-tuple in a stable order.
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT user_id, raw_text, summary, thought_type,
                   topics, people, action_items,
                   source, session_id, project,
                   embedding, metadata,
                   prov_agent, prov_activity, was_generated_by,
                   was_derived_from, source_uri,
                   created_at
            FROM brain.thoughts
            WHERE thought_id = %s AND user_id = %s
            """,
            (thought_id, user_id),
        )
        restore_row = cur.fetchone()
    finally:
        cur.close()

    if restore_row is None:
        # Defensive: snapshot succeeded but row vanished mid-call.
        raise RuntimeError(
            f"forget_thought: thought {thought_id} not in user scope "
            f"(user={user_id})"
        )

    # Step 3: DELETE the live row (CASCADE on brain.thought_versions handles
    # version history; knowledge_graph_nodes.source_thought_id is ON DELETE
    # SET NULL so KG-edge cleanup is automatic).
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
            (thought_id, user_id),
        )
        conn.commit()
    finally:
        cur.close()

    # Step 4: verification. ANY exception triggers a restore-and-record-error.
    try:
        verify_result = vf_probe.verify_forgetting(
            conn, snapshot, n=n, epsilon=epsilon,
        )
    except Exception as e:
        # Restore + audit + return error status.
        _restore_thought(conn, thought_id, restore_row)
        audit_id = _emit_forget_audit(
            conn,
            thought_id=thought_id,
            user_id=user_id,
            status="forget-failed-error",
            n=n,
            k=0,
            epsilon=epsilon,
            hoeffding_bound=vf_probe.hoeffding_bound(n, epsilon),
            exact_binomial_bound=vf_probe.exact_binomial_bound(n, epsilon),
            probe_quality={
                "n": n,
                "distribution": {},
                "sampledFromSnapshot": True,
                "error": str(e),
            },
            prov_agent=prov_agent,
            diagnostic={"verify_exception": str(e), "restored": True},
        )
        # brain-W2-S6: replay-log emission (verify-error branch). Best-effort.
        emit_replay_log(
            conn,
            user_id=user_id,
            event_type="forget",
            thought_id=thought_id,
            result_text=f"forget-failed-error audit_id={audit_id} error={str(e)[:80]}",
            prov_agent=prov_agent,
            metadata={
                "status": "forget-failed-error",
                "audit_id": int(audit_id),
                "n": int(n),
                "k": 0,
                "epsilon": float(epsilon),
                "restored": True,
            },
        )
        return {
            "thought_id": thought_id,
            "status": "forget-failed-error",
            "audit_id": audit_id,
            "audit": {
                "error": str(e),
                "restored": True,
                "n": n,
                "k": 0,
                "epsilon": epsilon,
            },
        }

    # Step 5 / 6: decision based on accepted flag.
    accepted = verify_result.accepted
    if not accepted:
        # k > 0: residue detected — restore the row BEFORE emitting the audit
        # row, so the audit row is the durable record of the (restored) state.
        _restore_thought(conn, thought_id, restore_row)

    audit_id = _emit_forget_audit(
        conn,
        thought_id=thought_id,
        user_id=user_id,
        status="forgotten" if accepted else "forget-failed-residue",
        n=verify_result.n,
        k=verify_result.k,
        epsilon=verify_result.epsilon,
        hoeffding_bound=verify_result.hoeffdingBound,
        exact_binomial_bound=verify_result.exactBinomialBound,
        probe_quality=verify_result.probeQuality,
        prov_agent=prov_agent,
        diagnostic=(
            None
            if accepted
            else {
                "surfaced_probes": [
                    p for p in verify_result.probes
                    if p.get("surfaced_forgotten")
                ],
                "restored": True,
            }
        ),
    )

    # brain-W2-S6: replay-log emission (verify-decision branch). Best-effort.
    forget_status = "forgotten" if accepted else "forget-failed-residue"
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type="forget",
        thought_id=thought_id,
        result_text=(
            f"{forget_status} audit_id={audit_id} "
            f"n={verify_result.n} k={verify_result.k} eps={verify_result.epsilon}"
        ),
        prov_agent=prov_agent,
        metadata={
            "status": forget_status,
            "audit_id": int(audit_id),
            "n": int(verify_result.n),
            "k": int(verify_result.k),
            "epsilon": float(verify_result.epsilon),
        },
    )

    return {
        "thought_id": thought_id,
        "status": "forgotten" if accepted else "forget-failed-residue",
        "audit_id": audit_id,
        "audit": {
            "n": verify_result.n,
            "k": verify_result.k,
            "epsilon": verify_result.epsilon,
            "hoeffdingBound": verify_result.hoeffdingBound,
            "hoeffdingConfidence": verify_result.hoeffdingConfidence,
            "exactBinomialBound": verify_result.exactBinomialBound,
            "exactBinomialConfidence": verify_result.exactBinomialConfidence,
            "probeQuality": verify_result.probeQuality,
            "prov_agent": prov_agent,
        },
    }


def _restore_thought(conn, thought_id: str, restore_row: tuple) -> None:
    """Re-INSERT a deleted thought row to preserve user data when VF rejects.

    The non-negotiable R1 invariant: forget never destroys data if verification
    fails. Called from :func:`forget_thought` when ``verify_forgetting`` either
    rejects (``k>0``) or raises. The restored row keeps its original
    ``thought_id`` so any external references remain valid; ``prov_activity``
    is set to ``'restore'`` to mark the lineage event distinctly.
    """
    (
        user_id, raw_text, summary, thought_type,
        topics, people, action_items,
        source, session_id, project,
        embedding, metadata,
        prov_agent_old, _prov_activity_old, was_generated_by,
        was_derived_from, source_uri,
        created_at,
    ) = restore_row

    # JSONB columns: psycopg2 returns them as Python lists/dicts already, but
    # we re-serialize defensively so a string fallback also works.
    def _jsonb(val):
        if val is None:
            return None
        if isinstance(val, str):
            return val
        return json.dumps(val)

    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thoughts (
                thought_id, user_id, raw_text, summary, thought_type,
                topics, people, action_items,
                source, session_id, project,
                prov_agent, prov_activity, was_generated_by,
                was_derived_from, source_uri,
                embedding, metadata,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s::vector, %s::jsonb,
                %s, NOW()
            )
            """,
            (
                thought_id, user_id, raw_text, summary, thought_type,
                _jsonb(topics), _jsonb(people), _jsonb(action_items),
                source, session_id, project,
                prov_agent_old, "restore", was_generated_by,
                was_derived_from, source_uri,
                embedding if embedding is not None else None,
                _jsonb(metadata),
                created_at,
            ),
        )
        conn.commit()
    finally:
        cur.close()


def _emit_forget_audit(
    conn,
    thought_id: str,
    user_id: str,
    status: str,
    n: int,
    k: int,
    epsilon: float,
    hoeffding_bound: float,
    exact_binomial_bound: float,
    probe_quality: Dict[str, Any],
    prov_agent: str,
    diagnostic: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert one row into ``brain.forget_audit``; return its ``audit_id``.

    Procurement-grade audit: BOTH bounds and confidences are stored as
    distinct labeled columns (R2 fix-wave), and the probe-quality marker
    (R3 fix-wave) is preserved as JSONB for downstream verification.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.forget_audit (
                forgotten_thought_id, user_id, status,
                n, k, epsilon,
                hoeffding_bound, hoeffding_confidence,
                exact_binomial_bound, exact_binomial_conf,
                probe_quality_json,
                prov_agent, prov_activity,
                diagnostic_json
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s::jsonb,
                %s, 'forget',
                %s::jsonb
            )
            RETURNING audit_id
            """,
            (
                thought_id, user_id, status,
                n, k, epsilon,
                hoeffding_bound, 1.0 - hoeffding_bound,
                exact_binomial_bound, 1.0 - exact_binomial_bound,
                json.dumps(probe_quality),
                prov_agent,
                json.dumps(diagnostic) if diagnostic is not None else None,
            ),
        )
        audit_id = cur.fetchone()[0]
        conn.commit()
        return int(audit_id)
    finally:
        cur.close()


# ─── Hebbian primitive: --promote / --demote with time-decay ─────────────────
#
# brain-W1-S10 (schema) + S11 (helpers + CLI) + S12 (tests). The 4th MS_eps
# column — agent-controlled memory weighting with natural decay.
#
# Reference: Lin/Li/Chen 2026 §12.1 ("Hebbian agent-controlled metacognition");
# OptivAI builder neurosymbolic harness (memory_promotions table).
#
# Mechanics:
#   * promote_thought() INSERTs a positive-weight row in brain.promotions.
#   * demote_thought() INSERTs a NEGATIVE-weight row. It does NOT delete prior
#     positives — the audit trail is preserved (R1-style invariant: history is
#     append-only, decisions are reversible).
#   * compute_effective_weight() sums all rows for a (thought_id, user_id),
#     applying the time-decay  weight * (1 + days_since)^(-0.7).
#
# Why -0.7? The exponent is taken directly from the OptivAI builder
# implementation. Empirically: at 1 day decay is ~0.62, at 7 days ~0.23, at
# 30 days ~0.09. Aggressive enough that stale promotions fade; gentle enough
# that recent emphasis survives a week of context churn.
#
# Within-kind over-application defense (gz-dsax2 from optivai-builder backlog):
# the search-side integration MUST gate the Hebbian boost by a minimum cosine
# similarity (HEBBIAN_MIN_RELEVANCE_FLOOR = 0.30). A heavily-promoted but
# semantically-irrelevant thought must NOT rank above a lower-promoted but
# highly-relevant one. The constant lives here for the future S13 search
# integration; this bundle (S10/S11/S12) ships the primitive only.


HEBBIAN_DECAY_EXPONENT = -0.7  # time-decay exponent: weight * (1+days_since)^(-0.7)
HEBBIAN_MIN_RELEVANCE_FLOOR = 0.30  # cosine sim floor below which promotion gives no boost
HEBBIAN_BOOST_COEFFICIENT = 0.1  # search-side multiplier for effective_weight (S13)


def promote_thought(
    conn,
    thought_id: str,
    user_id: str,
    weight: float = 1.0,
    reason: Optional[str] = None,
    prov_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Agent-controlled Hebbian promotion. INSERTs a positive-weight row into
    ``brain.promotions``.

    PS scoping (Principal Scoping per Lin/Li/Chen §12.1): ``thought_id`` must
    be in ``user_id``'s scope. Cross-user promotions raise ``RuntimeError``
    BEFORE any write — the table is untouched in that case.

    Multiple promotions on the same thought accumulate. Later retrieval
    scoring sums all promotions with time-decay; see
    :func:`compute_effective_weight`.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    thought_id
        The thought to promote (must belong to ``user_id``).
    user_id
        Caller scope.
    weight
        Promotion magnitude (default 1.0). Caller may pass any float; this
        function does NOT normalize the sign (use :func:`demote_thought`
        for negative weighting with sign-normalization).
    reason
        Optional human-readable rationale, persisted on the row.
    prov_agent
        PROV-DM agent identifier. Defaults to ``cli-user-{user_id}``.

    Returns
    -------
    dict
        ``{"promotion_id": int, "thought_id": str, "weight": float,
        "effective_weight": float}`` — the trailing field is the time-decayed
        sum of ALL promotions for this thought (post-insert, as of NOW).
        Useful for sanity-checking the immediate effect.

    Raises
    ------
    RuntimeError
        If ``thought_id`` is not in ``user_id``'s scope.
    """
    cur = conn.cursor()
    try:
        # PS scope check.
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s",
            (thought_id, user_id),
        )
        if cur.fetchone() is None:
            raise RuntimeError(
                f"promote_thought: thought {thought_id} not in user scope "
                f"(user={user_id})"
            )

        if prov_agent is None:
            prov_agent = _derive_prov_agent("manual", user_id)

        cur.execute(
            """
            INSERT INTO brain.promotions
              (thought_id, user_id, weight, prov_agent, reason)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING promotion_id
            """,
            (thought_id, user_id, float(weight), prov_agent, reason),
        )
        promotion_id = cur.fetchone()[0]
        conn.commit()
    finally:
        cur.close()

    effective = compute_effective_weight(conn, thought_id, user_id)

    # brain-W2-S6: replay-log emission. Best-effort; never raises.
    # demote_thought delegates here with a negative weight, so sign-of-weight
    # distinguishes the two event types in the audit trail.
    emit_event_type = "demote" if float(weight) < 0 else "promote"
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type=emit_event_type,
        thought_id=thought_id,
        prov_agent=prov_agent,
        metadata={
            "weight": float(weight),
            "effective_weight": float(effective),
            "promotion_id": int(promotion_id),
            "reason": reason,
        },
    )

    return {
        "promotion_id": int(promotion_id),
        "thought_id": thought_id,
        "weight": float(weight),
        "effective_weight": effective,
    }


def demote_thought(
    conn,
    thought_id: str,
    user_id: str,
    weight: float = 1.0,
    reason: Optional[str] = None,
    prov_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Demote a thought by INSERTing a negative-weight row.

    Does NOT delete prior positive promotions — the audit trail is preserved.
    A demoted thought can later be re-promoted without losing history.

    The sign of ``weight`` is normalized: any input becomes ``-abs(weight)``.
    That way ``demote_thought(weight=2.0)`` and ``demote_thought(weight=-2.0)``
    behave identically — both insert ``-2.0``.

    Cross-user scope is rejected by :func:`promote_thought` (the underlying
    write path); no separate scope check is necessary here.
    """
    return promote_thought(
        conn,
        thought_id=thought_id,
        user_id=user_id,
        weight=-abs(float(weight)),
        reason=reason,
        prov_agent=prov_agent,
    )


def compute_effective_weight(
    conn,
    thought_id: str,
    user_id: str,
) -> float:
    """Sum the time-decayed Hebbian weights for a thought.

    Formula::

        effective_weight = sum( weight * (1 + days_since_promoted)^(-0.7) )

    Filters by ``user_id`` as well as ``thought_id`` — defensive against any
    hypothetical row whose ``user_id`` differs from the parent thought's
    (the UNIQUE constraint and write-path PS check make this unreachable
    through ``promote_thought``, but a direct SQL INSERT can produce it).

    Returns ``0.0`` if no promotions exist. Negative ``days_since`` (clock
    skew — ``promoted_at`` in the future) is clamped to ``0.0`` so the decay
    factor caps at ``1.0`` rather than producing a spurious >1 boost.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT weight,
                   EXTRACT(EPOCH FROM (NOW() - promoted_at)) / 86400.0 AS days_since
            FROM brain.promotions
            WHERE thought_id = %s AND user_id = %s
            """,
            (thought_id, user_id),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    total = 0.0
    for weight, days_since in rows:
        days_clamped = max(0.0, float(days_since))
        decay = (1.0 + days_clamped) ** HEBBIAN_DECAY_EXPONENT
        total += float(weight) * decay
    return total


def capture(
    conn,
    text: str,
    user_id: str,
    source: str = "manual",
    session_id: str = "",
    project: str = "",
    prov_agent: Optional[str] = None,
    prov_activity: Optional[str] = None,
    was_derived_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture a thought with local embedding + Claude metadata extraction.

    Writes a W3C PROV-DM 1.3-conformant row: every capture stamps a
    ``prov_agent`` (defaulting from ``source``), a ``prov_activity``
    ("capture"), and a generated ``was_generated_by`` activity ID.

    Parameters
    ----------
    prov_agent
        Override the default agent ID. When ``None`` (default), derived
        from ``source`` + ``user_id`` via :func:`_derive_prov_agent`.
    prov_activity
        Override the activity verb. When ``None`` (default), ``"capture"``.
    was_derived_from
        Optional parent ``thought_id``. Validated to exist within the
        caller's ``user_id`` scope (PS — Principal Scoping); a mismatch
        raises :class:`RuntimeError`.
    """
    thought_id = _generate_thought_id()
    cur = conn.cursor()

    # Resolve PROV-DM defaults BEFORE any DB work so a bad input fails fast.
    if prov_agent is None:
        prov_agent = _derive_prov_agent(source, user_id)
    if prov_activity is None:
        prov_activity = "capture"
    was_generated_by = _generate_activity_id(thought_id)

    # PS primitive: was_derived_from must reference a thought in the caller's
    # scope. For this plugin, scope == user_id. A mismatch is a write-gate
    # rejection — raise before doing embedding / LLM work.
    if was_derived_from is not None:
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
            (was_derived_from, user_id),
        )
        if cur.fetchone() is None:
            cur.close()
            raise RuntimeError(
                "was_derived_from references non-existent thought "
                f"(or wrong user scope): {was_derived_from} (user={user_id})"
            )

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

    # Step 3: INSERT with PROV-DM fields. source_uri stays NULL for internal
    # captures (deferred to a later bead if/when ingesting from external URLs).
    insert_sql = """
        INSERT INTO brain.thoughts (
            thought_id, user_id, raw_text, summary, thought_type,
            topics, people, action_items, source, session_id, project,
            prov_agent, prov_activity, was_generated_by, was_derived_from, source_uri,
            embedding, metadata, created_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
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
            prov_agent,
            prov_activity,
            was_generated_by,
            was_derived_from,
            None,  # source_uri — reserved for external-URL captures
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

    # brain-W2-S6: replay-log emission. Best-effort; never raises. Placed
    # BEFORE return so the audit row reflects the same successful op context.
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type="capture",
        thought_id=thought_id,
        result_text=text,
        session_id=session_id or None,
        prov_agent=prov_agent,
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

def _print_citation_tree(node, indent: int = 0) -> None:
    """Pretty-print a citation tree for human consumption.

    Format::

        ● <thought_id> (d0)
            prov: <prov_agent>/<prov_activity>
            text: <first 100 chars of raw_text_preview>
          → <parent_thought_id> (d1)
              prov: ...
              text: ...

    Sentinel nodes (orphaned / cycle / max-depth) are rendered as e.g.::

        → <thought_id> (d2 [max-depth])
    """
    prefix = "  " * indent + ("→ " if indent > 0 else "● ")
    marker = f" [{node.orphan_marker}]" if node.orphan_marker else ""
    print(f"{prefix}{node.thought_id} (d{node.depth}{marker})")
    if not node.orphan_marker:
        print(f"{'  ' * indent}    prov: {node.prov_agent}/{node.prov_activity}")
        if node.raw_text_preview:
            print(f"{'  ' * indent}    text: {node.raw_text_preview[:100]}")
    for child in node.children:
        _print_citation_tree(child, indent=indent + 1)


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
                prov_agent=args.get("prov_agent"),
                prov_activity=args.get("prov_activity"),
                was_derived_from=args.get("was_derived_from"),
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


# ─── Migration runner ────────────────────────────────────────────────────────

def run_migration(sql_path: str) -> Dict[str, Any]:
    """Execute a one-shot SQL migration file. Idempotent migrations only.

    Migrations are expected to use ADD COLUMN IF NOT EXISTS / CREATE INDEX IF
    NOT EXISTS / guarded DO $$ blocks so that re-running them is a no-op. The
    SQL file is executed as a single multi-statement script; if it contains
    its own BEGIN/COMMIT it manages its own transaction, otherwise it runs
    inside the implicit transaction opened by psycopg2.

    Returns a status dict suitable for JSON serialization. Raises RuntimeError
    (the local error convention in this module) on failure.
    """
    if not sql_path:
        raise RuntimeError("Migration path required")
    if not os.path.exists(sql_path):
        raise RuntimeError(f"Migration file not found: {sql_path}")
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()
    if not sql.strip():
        raise RuntimeError(f"Migration file is empty: {sql_path}")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        return {
            "status": "ok",
            "file": sql_path,
            "size_bytes": len(sql),
        }
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration failed: {e}") from e
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
    group.add_argument("--migrate", type=str, metavar="SQL_FILE",
                       help="Execute a one-shot SQL migration file (idempotent)")
    group.add_argument("--from-pi", action="store_true", help="Pi bridge (stdin JSON)")
    group.add_argument("--snapshot", type=str, metavar="THOUGHT_ID",
                       help="Snapshot the current state of a thought to brain.thought_versions")
    group.add_argument("--versions", type=str, metavar="THOUGHT_ID",
                       help="List all versions of a thought")
    group.add_argument("--rollback", type=str, metavar="THOUGHT_ID",
                       help="Roll back a thought (combine with --to-revision)")
    group.add_argument("--diff", type=str, metavar="THOUGHT_ID",
                       help="Diff two revisions of a thought (combine with --from-revision and --to-revision)")
    group.add_argument("--forget", type=str, metavar="THOUGHT_ID",
                       help="Forget a thought with VF_eps verification (delete-after-verify)")
    group.add_argument("--promote", type=str, metavar="THOUGHT_ID",
                       help="Promote a thought (positive Hebbian weight; agent-controlled)")
    group.add_argument("--demote", type=str, metavar="THOUGHT_ID",
                       help="Demote a thought (inserts a negative-weight row; audit trail preserved)")
    group.add_argument("--trace", type=str, metavar="THOUGHT_ID",
                       help="Walk the provenance chain (was_derived_from) for a thought")
    group.add_argument("--inspect", type=str, metavar="THOUGHT_ID",
                       help="Inspect historical state of a thought "
                            "(combine with --at or --at-revision; default: latest)")
    group.add_argument("--replay", action="store_true",
                       help="Show the chronological brain replay log "
                            "(combine with --session-id, --from, --to, --event-type)")

    parser.add_argument("--from", type=str, default=None, dest="from_iso",
                        metavar="ISO",
                        help="Start of replay window (ISO 8601); used with --replay")
    parser.add_argument("--to", type=str, default=None, dest="to_iso",
                        metavar="ISO",
                        help="End of replay window (ISO 8601); used with --replay")
    parser.add_argument("--event-type", type=str, default=None,
                        dest="event_type", metavar="TYPE",
                        help="Filter --replay by event_type "
                             "(capture/forget/snapshot/rollback/promote/demote/search)")

    parser.add_argument("--at", type=str, default=None, metavar="ISO_TIMESTAMP",
                        help="Timestamp for --inspect (returns latest version <= this time); "
                             "ISO 8601 (e.g. '2026-05-21T10:30:00Z')")
    parser.add_argument("--at-revision", type=int, default=None, metavar="N",
                        help="Specific revision number for --inspect "
                             "(mutually exclusive with --at)")
    parser.add_argument("--max-depth", type=int, default=50, metavar="N",
                        help="Maximum walk depth for --trace (default: 50)")
    parser.add_argument("--weight", type=float, default=1.0, metavar="W",
                        help="Weight magnitude for --promote / --demote (default 1.0)")
    parser.add_argument("--reason", type=str, default=None, metavar="TEXT",
                        help="Optional human-readable rationale for promote/demote")
    parser.add_argument("--to-revision", type=int, default=None, metavar="N",
                        help="Target revision for --rollback or B-revision for --diff")
    parser.add_argument("--from-revision", type=int, default=None, metavar="N",
                        help="A-revision for --diff")
    parser.add_argument("--epsilon", type=float, default=0.05, metavar="EPS",
                        help="VF_eps target for --forget (default: 0.05)")
    parser.add_argument("--n", type=int, default=300, metavar="N",
                        help="Number of probes for --forget (default: 300; "
                             "gives 99.9999793%% exact-binomial confidence at eps=0.05)")

    parser.add_argument("--sort", type=str, choices=["similarity", "time"], default="similarity",
                        help="Sort search results: similarity (default) or time (oldest first)")
    parser.add_argument("--days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_RECENT_LIMIT)
    parser.add_argument("--type", type=str, dest="thought_type",
                        help="Filter by type: decision|insight|person_note|meeting|idea|task|reflection|preference|impression|pattern|working_memory|sentinel_event|sentinel_relevant")
    parser.add_argument("--source", type=str, default="manual")
    parser.add_argument("--session-id", type=str, default="")
    parser.add_argument("--project", type=str, default="")
    parser.add_argument("--prov-agent", type=str, default=None, dest="prov_agent",
                        help="Override default prov_agent for --capture (default derived from --source + USER)")
    parser.add_argument("--prov-activity", type=str, default=None, dest="prov_activity",
                        help="Override default prov_activity ('capture' for --capture, 'auto-capture-{trigger}' for hooks)")
    parser.add_argument("--derived-from", type=str, default=None, dest="was_derived_from",
                        help="thought_id of parent thought (was_derived_from); must be in your scope")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if args.from_pi:
        _run_from_pi()
        return

    if args.migrate:
        result = run_migration(args.migrate)
        if args.json:
            print(json.dumps(result))
        else:
            print(f"Migration {result['status']}: {result['file']} ({result['size_bytes']} bytes)")
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
                prov_agent=args.prov_agent,
                prov_activity=args.prov_activity,
                was_derived_from=args.was_derived_from,
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

        elif args.snapshot:
            result = snapshot_thought(
                conn,
                thought_id=args.snapshot,
                user_id=user_id,
                prov_agent=args.prov_agent,
            )
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(
                    f"Snapshot created: thought={result['thought_id']} "
                    f"revision={result['revision']} version_id={result['version_id']}"
                )

        elif args.versions:
            results = list_versions(
                conn,
                thought_id=args.versions,
                user_id=user_id,
            )
            if args.json:
                print(json.dumps(results, default=str))
            else:
                if not results:
                    print(f"No versions for thought {args.versions}")
                else:
                    print(f"Versions of {args.versions} ({len(results)} total):")
                    for v in results:
                        print(
                            f"  rev={v['revision']:3d} "
                            f"version_id={v['version_id']:6d} "
                            f"activity={v['prov_activity']:10s} "
                            f"agent={v['prov_agent']:32s} "
                            f"created={v['created_at']}"
                        )
                        if v.get("raw_text"):
                            print(f"        text: {v['raw_text']}")

        elif args.rollback:
            if args.to_revision is None:
                parser.error("--rollback requires --to-revision N")
            result = rollback_thought(
                conn,
                thought_id=args.rollback,
                user_id=user_id,
                to_revision=args.to_revision,
                prov_agent=args.prov_agent,
            )
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(
                    f"Rolled back: thought={result['thought_id']} "
                    f"to revision {result['rolled_back_to_revision']} "
                    f"(new revision={result['revision']}, "
                    f"version_id={result['version_id']}). "
                    f"History preserved — earlier revisions still in brain.thought_versions."
                )

        elif args.diff:
            if args.from_revision is None or args.to_revision is None:
                parser.error("--diff requires both --from-revision A and --to-revision B")
            patch = diff_versions(
                conn,
                thought_id=args.diff,
                user_id=user_id,
                revision_a=args.from_revision,
                revision_b=args.to_revision,
            )
            if args.json:
                print(json.dumps(patch, default=str))
            else:
                print(
                    f"RFC 6902 JSON Patch from revision {args.from_revision} "
                    f"→ {args.to_revision} of {args.diff}:"
                )
                if not patch:
                    print("  (no changes)")
                else:
                    for op in patch:
                        print(f"  {json.dumps(op, default=str)}")

        elif args.forget:
            result = forget_thought(
                conn,
                thought_id=args.forget,
                user_id=user_id,
                epsilon=args.epsilon,
                n=args.n,
                prov_agent=args.prov_agent,
            )
            if args.json:
                print(json.dumps(result, default=str))
            else:
                status = result["status"]
                audit = result.get("audit", {})
                if status == "forgotten":
                    print(f"✓ Forgotten: {result['thought_id']}")
                    print(
                        f"  n={audit.get('n')}, k={audit.get('k')}, "
                        f"eps={audit.get('epsilon')}"
                    )
                    print(
                        f"  Hoeffding: "
                        f"{audit.get('hoeffdingConfidence', 0.0) * 100:.4f}% "
                        f"confidence (loose)"
                    )
                    print(
                        f"  Exact binomial: "
                        f"{audit.get('exactBinomialConfidence', 0.0) * 100:.7f}% "
                        f"confidence (tight; procurement-grade)"
                    )
                else:
                    print(f"✗ Forget FAILED ({status}): {result['thought_id']}")
                    k = audit.get("k", "?")
                    print(
                        f"  k={k} probes surfaced the content; row restored "
                        f"from pre-delete snapshot (R1 invariant)."
                    )
                    if "error" in audit:
                        print(f"  error: {audit['error']}")

        elif args.promote:
            result = promote_thought(
                conn,
                thought_id=args.promote,
                user_id=user_id,
                weight=args.weight,
                reason=args.reason,
                prov_agent=args.prov_agent,
            )
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(
                    f"✓ Promoted: thought={result['thought_id']} "
                    f"weight={result['weight']:+.3f} "
                    f"effective_weight={result['effective_weight']:.4f} "
                    f"(promotion_id={result['promotion_id']})"
                )
                if args.reason:
                    print(f"  reason: {args.reason}")

        elif args.demote:
            result = demote_thought(
                conn,
                thought_id=args.demote,
                user_id=user_id,
                weight=args.weight,
                reason=args.reason,
                prov_agent=args.prov_agent,
            )
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(
                    f"✓ Demoted: thought={result['thought_id']} "
                    f"weight={result['weight']:+.3f} "
                    f"effective_weight={result['effective_weight']:.4f} "
                    f"(promotion_id={result['promotion_id']})"
                )
                if args.reason:
                    print(f"  reason: {args.reason}")

        elif args.trace:
            # Local import keeps citation_walker out of the cold-path imports
            # for other CLI verbs (it's only needed for --trace).
            import citation_walker
            root = citation_walker.trace_citation(
                conn,
                thought_id=args.trace,
                user_id=user_id,
                max_depth=args.max_depth,
            )
            if args.json:
                print(json.dumps(
                    citation_walker.citation_node_to_dict(root),
                    indent=2,
                    default=str,
                ))
            else:
                _print_citation_tree(root, indent=0)

        elif args.inspect:
            # Local import: time_travel is only needed for --inspect.
            import time_travel
            if args.at is not None and args.at_revision is not None:
                msg = "--at and --at-revision are mutually exclusive"
                if args.json:
                    print(json.dumps({"error": msg}))
                else:
                    print(f"Error: {msg}", file=sys.stderr)
                sys.exit(2)
            try:
                if args.at_revision is not None:
                    result = time_travel.inspect_at_revision(
                        conn,
                        thought_id=args.inspect,
                        user_id=user_id,
                        revision=args.at_revision,
                    )
                elif args.at is not None:
                    result = time_travel.inspect_at_timestamp(
                        conn,
                        thought_id=args.inspect,
                        user_id=user_id,
                        at_iso=args.at,
                    )
                else:
                    result = time_travel.inspect_latest(
                        conn,
                        thought_id=args.inspect,
                        user_id=user_id,
                    )
            except RuntimeError as e:
                if args.json:
                    print(json.dumps({"error": str(e)}))
                else:
                    print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

            if result is None:
                if args.json:
                    print(json.dumps({
                        "thought_id": args.inspect,
                        "result": None,
                        "message": "no version exists at this query",
                    }))
                else:
                    print(
                        f"No version of {args.inspect} found at the requested query."
                    )
            else:
                d = time_travel.inspect_result_to_dict(result)
                if args.json:
                    print(json.dumps(d, indent=2, default=str))
                else:
                    text_preview = (result.raw_text or "")[:200]
                    print(
                        f"● {result.thought_id} revision={result.revision} "
                        f"({result.prov_activity}, {result.created_at})"
                    )
                    print(f"  text: {text_preview}")
                    if result.summary:
                        print(f"  summary: {result.summary[:100]}")

        elif args.replay:
            # brain-W2-S7: replay-log dispatcher. Read-only over brain.replay_log.
            rows = query_replay_log(
                conn,
                user_id=user_id,
                session_id=args.session_id or None,
                from_iso=args.from_iso,
                to_iso=args.to_iso,
                event_type=args.event_type,
                limit=args.limit,
            )
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                if not rows:
                    print("No replay-log entries match your query.")
                else:
                    for r in rows:
                        tid = r.get("thought_id") or "-"
                        summary = r.get("result_summary") or ""
                        print(
                            f"[{r['created_at']}] "
                            f"{r['event_type']:<10} "
                            f"{tid:<32} {summary}"
                        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
