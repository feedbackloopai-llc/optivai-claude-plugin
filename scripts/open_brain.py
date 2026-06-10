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


# ─── PII + secrets redaction (redact-S7 — was brain-W2-S6) ───────────────────
# Defense-in-depth redaction pipeline (replaces the prior 4-pattern PII regex).
# Vendored from gz-redact under scripts/redact/ — bead redact-S7.
# Pipeline order:
#   1. secrets_redactors (8+ categories — AWS, Anthropic, OpenAI, GitHub, Slack,
#      Stripe, JWT, PEM private keys, plus several more)
#   2. pii_redactors (7 categories — email, phone, SSN, PAN+Luhn, IPv4, IPv6, DOB)
#   3. ContextRedactor wrapping EntropyRedactor — catches high-entropy unknown
#      tokens near keywords like 'password' / 'api_key', suppresses low-confidence
#      noise in unrelated prose.
#
# The redactor runs at the replay-log emitter boundary BEFORE any write, so
# brain.replay_log rows never contain raw PII (the pii_distinct DEFAULT TRUE
# column marker is the auditable assertion of this discipline).

import sys as _sys
_redact_path = os.path.dirname(os.path.abspath(__file__))
if _redact_path not in _sys.path:
    _sys.path.insert(0, _redact_path)
from redact import (
    compose,
    redact as _redact_compose,
    secrets_redactors,
    pii_redactors,
    EntropyRedactor,
    ContextRedactor,
)

# Composed pipeline — built once at module load (immutable).
_REDACT_PIPELINE = compose([
    *secrets_redactors,
    *pii_redactors,
    ContextRedactor(
        inner=EntropyRedactor(),  # default opts (min_entropy=4.58, base confidence 0.4)
        keywords=["password", "secret", "api_key", "token", "key", "cred"],
        radius=15,
        upgraded_confidence=0.85,
        suppress_low_confidence=0.6,
    ),
])


def redact_pii(text: Optional[str]) -> Optional[str]:
    """Defense-in-depth redaction via the composed gz-redact-derived pipeline.

    Same signature as the original 4-pattern function, so existing callsites
    (emit_replay_log, etc.) work unchanged. Now catches:
      - 8+ secret categories (AWS, Anthropic, OpenAI, GitHub, Slack, Stripe,
        JWT, PEM private keys, plus GCP, HuggingFace, GitLab, Twilio, SendGrid,
        HubSpot, Atlassian, bearer headers, basic-auth URLs)
      - 8 PII categories (email, phone, SSN, Luhn-validated PAN, IPv4,
        IPv6 full, IPv6 compressed (::), DOB)
      - High-entropy unknown tokens near 'password' / 'api_key' / similar keywords

    Returns None if input is None (preserves the old contract).
    """
    if text is None:
        return None
    return _redact_compose(text, _REDACT_PIPELINE)


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
    """Extract structured metadata using Claude API (primary).

    ``text`` MUST already be redacted before this is called.  The caller
    (``_extract_metadata`` / ``capture``) is responsible for applying
    ``redact_pii`` before dispatch.  This function trusts its input.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()
        safe_text = text[:4000]
        # Slice first (cap at 4000 chars), then brace-escape the slice so
        # str.format() cannot misinterpret user content as format
        # placeholders. Escaping after the slice is correct: the cap bounds
        # the prompt cost on the original text; the escape only expands
        # brace chars, which str.format() collapses back to single braces.
        escaped_text = safe_text.replace("{", "{{").replace("}", "}}")
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=escaped_text)
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
    """Ensure Ollama is installed, running, and has the required model.

    This function will NOT auto-install Ollama.  If Ollama is not present on
    PATH the caller receives ``False`` along with an actionable message on
    stderr.  Auto-installing via a remote shell script is a silent
    remote-code-execution vector and has been removed permanently.
    """
    import subprocess
    import urllib.request

    # Check if Ollama is installed — do NOT auto-install
    if not any(
        (Path(p) / "ollama").exists()
        for p in os.environ.get("PATH", "").split(":")
    ):
        print(
            "Ollama not available; install manually from https://ollama.com "
            "or set a different metadata provider",
            file=sys.stderr,
        )
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
    """Extract structured metadata using local Ollama (fallback 1).

    ``text`` MUST already be redacted before this is called.  See
    ``_extract_metadata_via_claude`` for the contract.
    """
    try:
        if not _ensure_ollama_ready():
            return None
        import urllib.request
        safe_text = text[:4000]
        # Brace-escape to prevent KeyError / silent prompt-injection seam.
        escaped_text = safe_text.replace("{", "{{").replace("}", "}}")
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=escaped_text)
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
    """Extract structured metadata using OpenAI API (fallback 2).

    ``text`` MUST already be redacted before this is called.  See
    ``_extract_metadata_via_claude`` for the contract.
    """
    try:
        import urllib.request
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        safe_text = text[:4000]
        # Brace-escape to prevent KeyError / silent prompt-injection seam.
        escaped_text = safe_text.replace("{", "{{").replace("}", "}}")
        prompt = METADATA_EXTRACTION_PROMPT.format(thought_text=escaped_text)
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
    """Get PostgreSQL connection string with priority: env → Keychain → config file.

    Resolution order:
      (a) DATABASE_URL environment variable — highest priority, never overridden.
      (b) macOS Keychain via ``security find-generic-password``.  The service name
          is read from OPEN_BRAIN_DB_KEYCHAIN_SERVICE (default:
          "optivai-neon-database-url") and the account from $USER.  Any subprocess
          error or empty result is treated as a miss and falls through to (c).
      (c) ~/.claude/hooks/auto-logger-config.json ``postgresql.connection_string``
          — last resort; emits a deprecation WARNING to stderr so the operator
          knows to migrate.
    """
    import subprocess

    # (a) Env var — highest priority.
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # (b) macOS Keychain.
    service = os.environ.get("OPEN_BRAIN_DB_KEYCHAIN_SERVICE", "optivai-neon-database-url")
    account = os.environ.get("USER") or os.environ.get("USERNAME") or os.environ.get("LOGNAME") or ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            keychain_url = result.stdout.strip()
            if keychain_url:
                return keychain_url
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # security binary absent (non-macOS) or timed out — fall through.
        pass

    # (c) Config file — deprecated fallback.
    config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            pg = config.get("destinations", {}).get("postgresql", {})
            conn_str = pg.get("connection_string", "")
            if conn_str:
                print(
                    "WARNING: auto-logger-config.json credential is deprecated; "
                    "migrate to Keychain or DATABASE_URL env",
                    file=sys.stderr,
                )
                return conn_str
        except (json.JSONDecodeError, OSError):
            pass

    raise RuntimeError(
        "No DATABASE_URL env var, no Keychain entry for service "
        f"'{service}' (override via OPEN_BRAIN_DB_KEYCHAIN_SERVICE), "
        "and no postgresql.connection_string in auto-logger-config.json"
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


# ─── Connected provenance graph (gz-0l68v) ───────────────────────────────────
#
# Closed, CLI-validated vocabulary of typed link kinds between atoms.
#
# This is a Python-side gate enforced BEFORE INSERT — the column itself is
# VARCHAR not an ENUM so that extending the vocabulary doesn't require an
# ALTER TYPE migration. Add a new kind here, ship the code change, the schema
# is unchanged. Document each kind so callers can pick the right one.
LINK_TYPES = {
    # Atom DAG (mirrors PROV-DM was_derived_from but typed)
    "derives_from",
    # Decision substrate
    "rationale_for",            # source is the rationale supporting target decision
    "alternative_rejected_by",  # source was rejected as an alternative by target decision
    # Verification / dispute substrate
    "verifies",                 # source verifies target's claim
    "refutes",                  # source refutes target (strong contradiction)
    "contradicts",              # source contradicts target (weaker than refutes)
    "resolves",                 # source resolves target — load-bearing for unresolved-findings query
    "supersedes",               # source supersedes target (target is stale)
    # Cross-surface bridges
    "references_bead",          # source references a bead (target_id like gz-XXXXX)
    "cites",                    # source cites target as a source or quotation
}


# Standalone DDL for the connected-provenance-graph table. Kept here as a
# fallback so a fresh install without the sql/ tree (or with a stale tree)
# can still get the table created at --init time. The same DDL also lives
# canonically in sql/BRAIN_SCHEMA_PG.sql.
_ATOM_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS brain.atom_links (
    link_id     BIGSERIAL         NOT NULL PRIMARY KEY,
    source_id   VARCHAR(64)       NOT NULL,
    target_id   VARCHAR(64)       NOT NULL,
    link_type   VARCHAR(32)       NOT NULL,
    prov        JSONB,
    user_id     VARCHAR(100)      NOT NULL,
    created_at  TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    CONSTRAINT atom_links_unique UNIQUE (source_id, target_id, link_type, user_id),
    CONSTRAINT atom_links_source_fk
      FOREIGN KEY (source_id) REFERENCES brain.thoughts(thought_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS atom_links_source_idx ON brain.atom_links(source_id);
CREATE INDEX IF NOT EXISTS atom_links_target_idx ON brain.atom_links(target_id);
CREATE INDEX IF NOT EXISTS atom_links_type_idx   ON brain.atom_links(link_type);
CREATE INDEX IF NOT EXISTS atom_links_user_idx   ON brain.atom_links(user_id);
"""


# ─── Core Operations ─────────────────────────────────────────────────────────

def init_schema(conn) -> str:
    """Create the brain schema and thoughts table if they don't exist."""
    ddl_path = Path(__file__).parent.parent / "sql" / "BRAIN_SCHEMA_PG.sql"
    if not ddl_path.exists():
        ddl_path = Path.home() / ".claude" / "sql" / "BRAIN_SCHEMA_PG.sql"
    if not ddl_path.exists():
        # No SQL file found — fall back to the minimal in-code DDL for the
        # atom_links table only (other tables require the full SQL file).
        cur = conn.cursor()
        try:
            cur.execute(_ATOM_LINKS_DDL)
            conn.commit()
            cur.close()
            return ("DDL file not found (checked repo and ~/.claude/sql/); "
                    "in-code atom_links DDL applied as partial fallback.")
        except Exception as e:
            conn.rollback()
            cur.close()
            return f"DDL file not found and in-code fallback failed: {e}"

    cur = conn.cursor()
    ddl = ddl_path.read_text(encoding="utf-8")

    try:
        cur.execute(ddl)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"DDL warning: {e}")
        return f"Schema initialization had warnings: {e}"

    # Defensive idempotent re-apply of atom_links DDL — covers the case
    # where the on-disk SQL file is older than this script (e.g. a partial
    # upgrade where open_brain.py is current but BRAIN_SCHEMA_PG.sql is
    # stale). The DDL uses IF NOT EXISTS so re-running is a no-op when the
    # table already exists.
    try:
        cur.execute(_ATOM_LINKS_DDL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"atom_links DDL defensive re-apply warning: {e}")

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

    gz-1t9l2 — result is clamped to 100 chars to fit the VARCHAR(100)
    column on every prov_agent storage path (brain.thoughts.prov_agent,
    brain.promotions.prov_agent, brain.thought_versions.prov_agent). A
    long ``user_id`` (>= 92 chars after the "cli-user-" prefix) would
    otherwise overflow the column at INSERT time.
    """
    if not source or source == "manual":
        result = f"cli-user-{user_id}"
    elif source == "pi" or source.startswith("pi-"):
        result = "pi-agent"
    elif source == "claude-code":
        result = "claude-code"
    elif source.startswith("hook-"):
        result = f"claude-code-hook-{source[5:]}"
    else:
        result = f"source-{source}"
    return result[:100]


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
    try:
        # gz-y0zmq — snapshot concurrent race defense. The PS scope check
        # below uses SELECT ... FOR UPDATE so a second concurrent snapshot
        # of the same thought blocks until this one commits, serializing
        # the (max-revision + 1) → INSERT sequence against the UNIQUE
        # (thought_id, revision) constraint.
        cur.execute(
            """
            SELECT raw_text, summary, thought_type, topics, people, action_items,
                   embedding, metadata
            FROM brain.thoughts
            WHERE thought_id = %s AND user_id = %s
            FOR UPDATE
            """,
            (thought_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
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
    except Exception:
        # gz-af9kn — guarantee rollback + cursor cleanup even when the
        # JSON-patch / embedding-serialization paths above raise. Without
        # this the cursor + tx state leaks back to the caller's conn.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass

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
    try:
        # gz-y0zmq — same concurrent-race defense as snapshot_thought:
        # take a row-level lock on the parent thought so concurrent
        # rollbacks (or snapshot + rollback) of the same thought serialize
        # against the UNIQUE (thought_id, revision) constraint.
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id=%s AND user_id=%s FOR UPDATE",
            (thought_id, user_id),
        )
        if cur.fetchone() is None:
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
    except Exception:
        # gz-af9kn — match snapshot_thought's defensive cleanup. Rollback
        # the tx and re-raise; finally clause closes the cursor.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass

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
# brain-W1-S8 / fblai-152r8. Implements the verified-forgetting flow.
#
# RESIDUE SURFACES (what survives a DELETE of brain.thoughts today, pre-fix):
#
#   brain.thought_versions   — FK ON DELETE CASCADE in DDL, but live DB may not
#                              have the constraint applied; we EXPLICIT-DELETE to
#                              guarantee coverage regardless. GUARANTEED restore.
#   brain.knowledge_graph_nodes — source_thought_id SET NULL (node stays active);
#                              thought's own node (node_nk="thought:<id>") survives
#                              with its edges. We delete thought-type node + its
#                              edges ONLY IF it has no other incoming edges from
#                              live thoughts (shared topic/person/project nodes
#                              are NOT deleted — they may be referenced elsewhere).
#                              BEST-EFFORT restore (re-insert node + edges).
#   brain.replay_log         — rows survive with thought_id & content in fields.
#                              REDACT IN PLACE (NULL query_redacted + result_summary,
#                              set tombstone flag in metadata). NOT deleted — audit
#                              integrity preserved. Restore = un-redact from snapshot.
#                              BEST-EFFORT restore.
#   brain.atom_links inbound — rows with target_id = forgotten thought_id survive
#                              as orphans (no FK on target_id). Mark them orphaned
#                              by setting a metadata flag; they remain detectable
#                              via --query-orphan-links. NOT deleted.
#                              BEST-EFFORT restore (un-mark flag).
#
# GUARANTEED restore surfaces: {thoughts row, thought_versions rows}
# BEST-EFFORT restore surfaces: {kg node + edges, replay_log redactions,
#                                 atom_links orphan markers}
#   If best-effort restore of a surface fails, the audit row records the failure;
#   the caller receives status='forget-failed-residue' or 'forget-failed-error'
#   with the partial-restore detail in diagnostic_json.
#
# SCRUB ORDER (FK-safe):
#   1. Explicit DELETE brain.thought_versions WHERE thought_id = X
#   2. Redact-in-place brain.replay_log WHERE thought_id = X
#   3. Mark orphans: brain.atom_links WHERE target_id = X (no FK; mark, not delete)
#   4. KG: delete edges touching the thought's own node, then delete the node IF
#      no other thought references it (edge-count check from live thoughts)
#   5. DELETE brain.thoughts WHERE thought_id = X (and CASCADE covers anything left)
#
# PROBE ORDER (Half-B, fblai-152r8): verify_forgetting now also runs per-surface
#   presence probes via the surface_residue_probes argument so k=0 measures real
#   absence across independent surfaces, not just the already-deleted main row.
#
# Audit log (brain.forget_audit) records BOTH bounds distinctly (R2 fix-wave):
#   - hoeffding_bound / hoeffding_confidence (loose; 77.69% at n=300/eps=0.05)
#   - exact_binomial_bound / exact_binomial_conf (tight; 99.9999793%)
#
# IMPORTANT: The binomial confidence bound conditions on probe SENSITIVITY across
# the scrubbed surfaces. It is NOT an unconditional statistical guarantee about
# arbitrary residue channels. If a Half-A scrub silently fails, the corresponding
# Half-B surface probe surfaces it (k>0) and the restore path fires — the proof
# that the guarantee is real, not theater. The bound is meaningful only insofar as
# the probe set covers the relevant residue surfaces.


def _scrub_residue_surfaces(
    conn,
    thought_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Scrub residue surfaces BEFORE deleting brain.thoughts (fblai-152r8 Half-A).

    Called pre-DELETE so we can snapshot enough state to restore each surface
    if verification later fails. Returns a ``scrub_snapshot`` dict used by
    :func:`_restore_residue_surfaces`.

    Scrub order is FK-safe: versions first, then replay redaction, then orphan
    marking, then KG cleanup, and finally the main row deletion happens in the
    caller (:func:`forget_thought`).

    Returns
    -------
    dict with keys:
        versions_snapshot     list of dicts (one per thought_versions row)
        replay_rows_snapshot  list of dicts (one per replay_log row to redact)
        kg_node_id            str | None  (the thought's own KG node id, if any)
        kg_node_snapshot      dict | None (full node row: node_type, definition,
                              lifecycle_status, created_at, etc. for lossless restore)
        kg_edges_snapshot     list of dicts (edges involving the thought node)
        inbound_link_ids      list of int (atom_links.link_id for inbound targets)
        scrub_counts          dict of surface -> int (rows affected per surface)
    """
    scrub_counts: Dict[str, int] = {
        "thought_versions": 0,
        "replay_log_redacted": 0,
        "atom_links_orphaned": 0,
        "kg_node_deleted": 0,
        "kg_edges_deleted": 0,
    }
    versions_snapshot: list = []
    replay_rows_snapshot: list = []
    kg_node_id: Optional[str] = None
    kg_node_snapshot: Optional[Dict[str, Any]] = None  # H3: full node row for restore
    kg_edges_snapshot: list = []
    inbound_link_ids: list = []

    cur = conn.cursor()
    try:
        # ── 1. Snapshot + DELETE thought_versions (GUARANTEED surface) ────────
        # Explicit DELETE regardless of FK CASCADE status in live DB.
        cur.execute(
            """
            SELECT version_id, revision, raw_text, summary, thought_type,
                   topics, people, action_items, embedding, metadata,
                   prov_agent, prov_activity, parent_version, diff_json,
                   created_at
            FROM brain.thought_versions
            WHERE thought_id = %s
            ORDER BY revision ASC
            """,
            (thought_id,),
        )
        for row in cur.fetchall():
            versions_snapshot.append({
                "version_id": row[0], "revision": row[1], "raw_text": row[2],
                "summary": row[3], "thought_type": row[4], "topics": row[5],
                "people": row[6], "action_items": row[7], "embedding": row[8],
                "metadata": row[9], "prov_agent": row[10],
                "prov_activity": row[11], "parent_version": row[12],
                "diff_json": row[13], "created_at": row[14],
            })
        cur.execute(
            "DELETE FROM brain.thought_versions WHERE thought_id = %s",
            (thought_id,),
        )
        scrub_counts["thought_versions"] = len(versions_snapshot)
        conn.commit()

        # ── 2. Redact-in-place replay_log rows (BEST-EFFORT surface) ─────────
        # Redact query_redacted + result_summary for rows referencing this thought.
        # We also ILIKE-scan text fields for the thought_id string to catch rows
        # that reference the thought by ID even without the thought_id FK column.
        cur.execute(
            """
            SELECT event_id, query_redacted, result_summary, metadata
            FROM brain.replay_log
            WHERE thought_id = %s AND user_id = %s
            """,
            (thought_id, user_id),
        )
        rows = cur.fetchall()
        for row in rows:
            replay_rows_snapshot.append({
                "event_id": row[0],
                "query_redacted": row[1],
                "result_summary": row[2],
                "metadata": row[3],
            })
        if rows:
            event_ids = [r[0] for r in rows]
            # Redact text fields and stamp tombstone in metadata.
            for eid in event_ids:
                cur.execute(
                    """
                    UPDATE brain.replay_log
                    SET query_redacted = NULL,
                        result_summary = '[redacted by VF_eps forget]',
                        metadata = COALESCE(metadata, '{}'::jsonb)
                                   || %s::jsonb
                    WHERE event_id = %s
                    """,
                    (json.dumps({"vf_eps_tombstone": True, "forgotten_thought_id": thought_id}),
                     eid),
                )
            scrub_counts["replay_log_redacted"] = len(event_ids)
            conn.commit()

        # ── 3. Mark inbound atom_links orphaned (BEST-EFFORT surface) ─────────
        # atom_links.target_id has no FK — inbound links to the forgotten thought
        # become dangling references. We mark (not delete) them so --query-orphan-links
        # can surface them, and so they can be un-marked if restore fires.
        cur.execute(
            """
            SELECT link_id
            FROM brain.atom_links
            WHERE target_id = %s AND user_id = %s
            """,
            (thought_id, user_id),
        )
        inbound_link_ids = [r[0] for r in cur.fetchall()]
        if inbound_link_ids:
            for lid in inbound_link_ids:
                cur.execute(
                    """
                    UPDATE brain.atom_links
                    SET prov = COALESCE(prov, '{}'::jsonb)
                               || %s::jsonb
                    WHERE link_id = %s
                    """,
                    (json.dumps({"vf_eps_orphaned": True, "forgotten_target": thought_id}),
                     lid),
                )
            scrub_counts["atom_links_orphaned"] = len(inbound_link_ids)
            conn.commit()

        # ── 4. KG: delete thought node + edges ONLY IF no other live thought
        #    references it (BEST-EFFORT surface) ─────────────────────────────
        # The thought's own KG node (node_nk = "thought:<id>") is distinct from
        # shared topic/person/project nodes. We look up its node_id, then check
        # if any OTHER live thoughts still reference nodes connected to this one.
        # The decision rule: delete the thought-type node + all its edges. DO NOT
        # delete topic/person/project nodes — they may be referenced by other
        # thoughts and are intentionally shared.
        try:
            cur.execute(
                """
                SELECT node_id
                FROM brain.knowledge_graph_nodes
                WHERE node_nk = %s AND user_id = %s
                """,
                (f"thought:{thought_id}", user_id),
            )
            node_row = cur.fetchone()
            if node_row is not None:
                kg_node_id = node_row[0]

                # Snapshot edges before deletion for restore.
                cur.execute(
                    """
                    SELECT edge_id, source_node, target_node, edge_type,
                           weight, user_id, created_at
                    FROM brain.knowledge_graph_edges
                    WHERE (source_node = %s OR target_node = %s)
                      AND user_id = %s
                    """,
                    (kg_node_id, kg_node_id, user_id),
                )
                for erow in cur.fetchall():
                    kg_edges_snapshot.append({
                        "edge_id": erow[0], "source_node": erow[1],
                        "target_node": erow[2], "edge_type": erow[3],
                        "weight": erow[4], "user_id": erow[5],
                        "created_at": erow[6],
                    })

                # Snapshot the node itself (H3 review fix: fetchone() the result —
                # previously the SELECT ran but the row was discarded, so restore
                # had to re-derive the node from thoughts.summary, losing
                # node_type / definition / lifecycle_status / created_at).
                cur.execute(
                    """
                    SELECT node_id, node_nk, node_type, name, definition,
                           user_id, source_thought_id, lifecycle_status,
                           created_at, updated_at
                    FROM brain.knowledge_graph_nodes
                    WHERE node_id = %s
                    """,
                    (kg_node_id,),
                )
                node_snapshot_row = cur.fetchone()
                if node_snapshot_row is not None:
                    kg_node_snapshot = {
                        "node_id": node_snapshot_row[0],
                        "node_nk": node_snapshot_row[1],
                        "node_type": node_snapshot_row[2],
                        "name": node_snapshot_row[3],
                        "definition": node_snapshot_row[4],
                        "user_id": node_snapshot_row[5],
                        "source_thought_id": node_snapshot_row[6],
                        "lifecycle_status": node_snapshot_row[7],
                        "created_at": node_snapshot_row[8],
                        "updated_at": node_snapshot_row[9],
                    }

                # Delete edges first (FK source/target → nodes), then the node.
                if kg_edges_snapshot:
                    cur.execute(
                        """
                        DELETE FROM brain.knowledge_graph_edges
                        WHERE (source_node = %s OR target_node = %s)
                          AND user_id = %s
                        """,
                        (kg_node_id, kg_node_id, user_id),
                    )
                    scrub_counts["kg_edges_deleted"] = len(kg_edges_snapshot)

                cur.execute(
                    "DELETE FROM brain.knowledge_graph_nodes WHERE node_id = %s",
                    (kg_node_id,),
                )
                scrub_counts["kg_node_deleted"] = 1
                conn.commit()
        except Exception as kg_err:
            # KG tables may not exist in all deployments — fail-open.
            conn.rollback()
            logger.debug(f"_scrub_residue_surfaces: KG scrub skipped: {kg_err}")
            kg_node_id = None
            kg_node_snapshot = None
            kg_edges_snapshot = []

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {
        "versions_snapshot": versions_snapshot,
        "replay_rows_snapshot": replay_rows_snapshot,
        "kg_node_id": kg_node_id,
        "kg_node_snapshot": kg_node_snapshot,
        "kg_edges_snapshot": kg_edges_snapshot,
        "inbound_link_ids": inbound_link_ids,
        "scrub_counts": scrub_counts,
    }


def _restore_residue_surfaces(
    conn,
    thought_id: str,
    user_id: str,
    scrub_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Restore residue surfaces that were scrubbed by :func:`_scrub_residue_surfaces`.

    Called when VF verification rejects or errors — the forget is non-atomic and
    must be unwound. GUARANTEED restore: thought_versions. BEST-EFFORT restore:
    KG node + edges, replay_log un-redaction, atom_links un-orphaning.

    Returns a dict with per-surface restore counts, any errors encountered, and
    a top-level ``restore_complete`` bool (H1 review fix): True iff every
    surface restored without error. When False, the caller MUST surface a
    distinct status ('forget-failed-partial-restore') so a consumer is not
    misled into believing the graph is consistent.
    """
    restore_counts: Dict[str, Any] = {
        "thought_versions": 0,
        "replay_log_unredacted": 0,
        "atom_links_unorphaned": 0,
        "kg_node_restored": 0,
        "kg_edges_restored": 0,
        "errors": [],
        "restore_complete": True,  # H1: set False if any best-effort restore raises
    }

    cur = conn.cursor()
    try:
        # ── GUARANTEED: Re-insert thought_versions ────────────────────────────
        for v in scrub_snapshot.get("versions_snapshot", []):
            try:
                # Conditional vector cast (H2 review fix): schema-qualify
                # public.vector when non-NULL (Neon pooler search_path safety);
                # use a bare NULL literal when the version had no embedding so
                # NULL::vector cannot fail.
                v_embedding = v.get("embedding")
                if v_embedding is not None:
                    v_emb_sql = "%s::public.vector"
                else:
                    v_emb_sql = "NULL"
                # Re-insert with original version_id if bigserial allows it —
                # use INSERT ... ON CONFLICT DO NOTHING to be idempotent.
                cur.execute(
                    f"""
                    INSERT INTO brain.thought_versions (
                        version_id, thought_id, revision, raw_text, summary,
                        thought_type, topics, people, action_items,
                        embedding, metadata, prov_agent, prov_activity,
                        parent_version, diff_json, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        {v_emb_sql}, %s::jsonb, %s, %s,
                        %s, %s::jsonb, %s
                    )
                    ON CONFLICT (thought_id, revision) DO NOTHING
                    """,
                    (
                        v["version_id"], thought_id, v["revision"], v["raw_text"],
                        v["summary"], v["thought_type"],
                        json.dumps(v["topics"]) if v["topics"] is not None else None,
                        json.dumps(v["people"]) if v["people"] is not None else None,
                        json.dumps(v["action_items"]) if v["action_items"] is not None else None,
                    )
                    + ((v_embedding,) if v_embedding is not None else ())
                    + (
                        json.dumps(v["metadata"]) if v["metadata"] is not None else None,
                        v["prov_agent"], v["prov_activity"],
                        v["parent_version"],
                        json.dumps(v["diff_json"]) if v["diff_json"] is not None else None,
                        v["created_at"],
                    ),
                )
                restore_counts["thought_versions"] += 1
            except Exception as e:
                conn.rollback()
                restore_counts["errors"].append(f"versions restore: {e}")
        conn.commit()

        # ── BEST-EFFORT: Un-redact replay_log rows ────────────────────────────
        for r in scrub_snapshot.get("replay_rows_snapshot", []):
            try:
                cur.execute(
                    """
                    UPDATE brain.replay_log
                    SET query_redacted = %s,
                        result_summary = %s,
                        metadata = %s::jsonb
                    WHERE event_id = %s
                    """,
                    (
                        r["query_redacted"],
                        r["result_summary"],
                        json.dumps(r["metadata"]) if r["metadata"] is not None else None,
                        r["event_id"],
                    ),
                )
                restore_counts["replay_log_unredacted"] += 1
            except Exception as e:
                conn.rollback()
                restore_counts["errors"].append(f"replay_log restore event {r['event_id']}: {e}")
        conn.commit()

        # ── BEST-EFFORT: Un-orphan atom_links ────────────────────────────────
        for lid in scrub_snapshot.get("inbound_link_ids", []):
            try:
                # Remove the vf_eps_orphaned flag from the prov JSONB.
                cur.execute(
                    """
                    UPDATE brain.atom_links
                    SET prov = prov - 'vf_eps_orphaned' - 'forgotten_target'
                    WHERE link_id = %s
                    """,
                    (lid,),
                )
                restore_counts["atom_links_unorphaned"] += 1
            except Exception as e:
                conn.rollback()
                restore_counts["errors"].append(f"atom_links restore link {lid}: {e}")
        conn.commit()

        # ── BEST-EFFORT: Restore KG node + edges ─────────────────────────────
        try:
            node_id = scrub_snapshot.get("kg_node_id")
            node_snap = scrub_snapshot.get("kg_node_snapshot")
            if node_id is not None:
                # Re-insert the thought node (it was deleted, so no conflict
                # expected; use ON CONFLICT DO NOTHING for safety).
                cur.execute(
                    """
                    SELECT node_id FROM brain.knowledge_graph_nodes
                    WHERE node_id = %s
                    """,
                    (node_id,),
                )
                if cur.fetchone() is None:
                    # H3 review fix: rebuild the node from the FULL snapshot
                    # (node_type, definition, lifecycle_status, created_at,
                    # updated_at), not re-derived from thoughts.summary. Fall
                    # back to thoughts.summary only if the snapshot is missing.
                    if node_snap is not None:
                        cur.execute(
                            """
                            INSERT INTO brain.knowledge_graph_nodes
                                   (node_id, node_nk, node_type, name, definition,
                                    user_id, source_thought_id, lifecycle_status,
                                    created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (node_nk, user_id) DO NOTHING
                            """,
                            (
                                node_snap["node_id"], node_snap["node_nk"],
                                node_snap["node_type"], node_snap["name"],
                                node_snap["definition"], node_snap["user_id"],
                                node_snap["source_thought_id"],
                                node_snap["lifecycle_status"],
                                node_snap["created_at"], node_snap["updated_at"],
                            ),
                        )
                    else:
                        # Defensive fallback: snapshot missing (older scrub path).
                        cur.execute(
                            """
                            SELECT summary FROM brain.thoughts
                            WHERE thought_id = %s AND user_id = %s
                            """,
                            (thought_id, user_id),
                        )
                        summary_row = cur.fetchone()
                        node_name = (
                            (summary_row[0] or thought_id)[:200]
                            if summary_row else thought_id[:200]
                        )
                        cur.execute(
                            """
                            INSERT INTO brain.knowledge_graph_nodes
                                   (node_id, node_nk, node_type, name, user_id,
                                    source_thought_id, lifecycle_status)
                            VALUES (%s, %s, 'thought', %s, %s, %s, 'active')
                            ON CONFLICT (node_nk, user_id) DO NOTHING
                            """,
                            (node_id, f"thought:{thought_id}", node_name, user_id, thought_id),
                        )
                    restore_counts["kg_node_restored"] = 1

                # Re-insert edges.
                for edge in scrub_snapshot.get("kg_edges_snapshot", []):
                    cur.execute(
                        """
                        INSERT INTO brain.knowledge_graph_edges
                               (edge_id, source_node, target_node, edge_type,
                                weight, user_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (edge_id) DO NOTHING
                        """,
                        (
                            edge["edge_id"], edge["source_node"], edge["target_node"],
                            edge["edge_type"], edge["weight"], edge["user_id"],
                            edge["created_at"],
                        ),
                    )
                    restore_counts["kg_edges_restored"] += 1
            conn.commit()
        except Exception as e:
            conn.rollback()
            restore_counts["errors"].append(f"kg restore: {e}")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # H1 review fix: restore_complete is True iff no best-effort restore raised.
    restore_counts["restore_complete"] = len(restore_counts["errors"]) == 0
    return restore_counts


def forget_thought(
    conn,
    thought_id: str,
    user_id: str,
    epsilon: float = 0.05,
    n: int = 300,
    prov_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Forget a thought with VF_eps verification (Lin/Li/Chen 2026 §12.1).

    The delete-after-verify protocol (fblai-152r8 Half-A: residue scrub):

      1. Build ProbeSeedSnapshot capturing forgotten_text + top-50 NN
         neighbors PRE-DELETE (and PRE-scrub).
      2. Capture the full thought row for restore (user_id + all PROV columns).
      3. SCRUB residue surfaces (thought_versions explicit DELETE; replay_log
         redact-in-place; atom_links inbound orphan-mark; KG node+edges delete).
         Snapshot each surface for restore.
      4. DELETE the thought from brain.thoughts (CASCADE covers anything left).
      5. Run n probes against the post-scrub/post-delete live store — INCLUDING
         per-surface presence probes (fblai-152r8 Half-B). k=0 is now a real
         measurement across independent surfaces, not a tautology.
      6. If accepted (k=0): emit audit row with status='forgotten'. Done.
      7. If rejected (k>0) or verify errors: RESTORE all surfaces from snapshots
         (guaranteed: thought row + versions; best-effort: KG, replay, links).
         Emit audit row with status='forget-failed-residue' or 'forget-failed-error'.

    PS scoping (Principal Scoping per Lin §12.1): raises RuntimeError if
    ``thought_id`` is not in ``user_id``'s scope. Cross-user forgets are
    rejected at the snapshot step BEFORE any scrub — the live row remains
    untouched.

    GUARANTEE NOTE: The binomial confidence bound conditions on probe sensitivity
    across the scrubbed surfaces. It is NOT an unconditional guarantee about
    arbitrary residue channels. If a Half-A scrub silently fails, the corresponding
    Half-B surface probe surfaces it (k>0) and restore fires. The bound is
    meaningful only insofar as the probe set covers the relevant residue surfaces.

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
        Number of probes (default 300, the calibrated value yielding
        99.9999793% exact-binomial confidence at eps=0.05/k=0).
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

    # Step 3: SCRUB residue surfaces pre-DELETE (fblai-152r8 Half-A).
    # Explicit version scrub (guaranteed), replay redaction (best-effort),
    # inbound link orphan-marking (best-effort), KG node+edge deletion (best-effort).
    # scrub_snapshot carries per-surface data needed to restore on failure.
    scrub_snapshot = _scrub_residue_surfaces(conn, thought_id, user_id)

    # Step 4: DELETE the live thoughts row.
    # Note: thought_versions was already explicitly deleted above, and atom_links
    # outgoing (source=thought_id) will cascade here too; that is belt-and-suspenders.
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
            (thought_id, user_id),
        )
        conn.commit()
    finally:
        cur.close()

    # Step 5: verification. ANY exception triggers full restore-and-record-error.
    # Pass the scrub_snapshot so verify_forgetting can run per-surface probes.
    try:
        verify_result = vf_probe.verify_forgetting(
            conn, snapshot, n=n, epsilon=epsilon,
            scrub_snapshot=scrub_snapshot,
        )
    except Exception as e:
        # Restore thought row first (guaranteed), then residue surfaces (best-effort).
        _restore_thought(conn, thought_id, restore_row)
        restore_detail = _restore_residue_surfaces(conn, thought_id, user_id, scrub_snapshot)
        # H1 review fix: if a best-effort restore raised, the graph is left
        # inconsistent — surface a distinct status so consumers are not misled.
        restore_complete = bool(restore_detail.get("restore_complete", True))
        err_status = (
            "forget-failed-error" if restore_complete
            else "forget-failed-partial-restore"
        )
        audit_id = _emit_forget_audit(
            conn,
            thought_id=thought_id,
            user_id=user_id,
            status=err_status,
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
            scrub_counts=scrub_snapshot.get("scrub_counts", {}),
            paraphrase_degraded=False,
            actual_distribution={},
            restore_complete=restore_complete,
            diagnostic={
                "verify_exception": str(e),
                "restored": True,
                "restore_complete": restore_complete,
                "restore_detail": restore_detail,
            },
        )
        # brain-W2-S6: replay-log emission (verify-error branch). Best-effort.
        emit_replay_log(
            conn,
            user_id=user_id,
            event_type="forget",
            thought_id=thought_id,
            result_text=f"{err_status} audit_id={audit_id} error={str(e)[:80]}",
            prov_agent=prov_agent,
            metadata={
                "status": err_status,
                "audit_id": int(audit_id),
                "n": int(n),
                "k": 0,
                "epsilon": float(epsilon),
                "restored": True,
                "restore_complete": restore_complete,
            },
        )
        return {
            "thought_id": thought_id,
            "status": err_status,
            "audit_id": audit_id,
            "audit": {
                "error": str(e),
                "restored": True,
                "restore_complete": restore_complete,
                "n": n,
                "k": 0,
                "epsilon": epsilon,
            },
        }

    # Step 6 / 7: decision based on accepted flag.
    accepted = verify_result.accepted
    restore_complete = True  # vacuously true on accept (nothing restored)
    if not accepted:
        # k > 0: residue detected — restore thought row first (guaranteed),
        # then residue surfaces (best-effort). Audit row is the durable record.
        _restore_thought(conn, thought_id, restore_row)
        restore_detail = _restore_residue_surfaces(conn, thought_id, user_id, scrub_snapshot)
        # H1 review fix: a best-effort restore failure leaves the graph
        # inconsistent — surface a distinct status so consumers know the
        # rejected forget did not cleanly unwind.
        restore_complete = bool(restore_detail.get("restore_complete", True))
    else:
        restore_detail = None

    if accepted:
        forget_status = "forgotten"
    elif restore_complete:
        forget_status = "forget-failed-residue"
    else:
        forget_status = "forget-failed-partial-restore"

    audit_id = _emit_forget_audit(
        conn,
        thought_id=thought_id,
        user_id=user_id,
        status=forget_status,
        n=verify_result.n,
        k=verify_result.k,
        epsilon=verify_result.epsilon,
        hoeffding_bound=verify_result.hoeffdingBound,
        exact_binomial_bound=verify_result.exactBinomialBound,
        probe_quality=verify_result.probeQuality,
        prov_agent=prov_agent,
        scrub_counts=scrub_snapshot.get("scrub_counts", {}),
        paraphrase_degraded=verify_result.probeQuality.get("paraphrase_degraded", False),
        actual_distribution=verify_result.probeQuality.get("actual_distribution", {}),
        restore_complete=restore_complete,
        diagnostic=(
            None
            if accepted
            else {
                "surfaced_probes": [
                    p for p in verify_result.probes
                    if p.get("surfaced_forgotten")
                ],
                "restored": True,
                "restore_complete": restore_complete,
                "restore_detail": restore_detail,
            }
        ),
    )

    # brain-W2-S6: replay-log emission (verify-decision branch). Best-effort.
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
            "restore_complete": restore_complete,
        },
    )

    return {
        "thought_id": thought_id,
        "status": forget_status,
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
            "scrub_counts": scrub_snapshot.get("scrub_counts", {}),
            "restore_complete": restore_complete,
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
        # Use a conditional cast for the vector column: NULL::vector fails when
        # pgvector is not in the current schema search_path; cast only when non-NULL.
        # H2 review fix: schema-qualify as public.vector — Neon's connection pooler
        # can reset search_path between statements mid-transaction, so an
        # unqualified ::vector cast fails with "type vector does not exist" exactly
        # on the restore path (proven by test_nonnull_embedding_restored_on_failure).
        if embedding is not None:
            embedding_sql = "%s::public.vector"
            embedding_val = embedding
        else:
            embedding_sql = "NULL"
            embedding_val = None

        cur.execute(
            f"""
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
                {embedding_sql}, %s::jsonb,
                %s, NOW()
            )
            """,
            (
                thought_id, user_id, raw_text, summary, thought_type,
                _jsonb(topics), _jsonb(people), _jsonb(action_items),
                source, session_id, project,
                prov_agent_old, "restore", was_generated_by,
                was_derived_from, source_uri,
            )
            + ((embedding_val,) if embedding is not None else ())
            + (
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
    scrub_counts: Optional[Dict[str, Any]] = None,
    paraphrase_degraded: bool = False,
    actual_distribution: Optional[Dict[str, int]] = None,
    restore_complete: bool = True,
    diagnostic: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert one row into ``brain.forget_audit``; return its ``audit_id``.

    Procurement-grade audit (fblai-152r8 Half-C + review-fix honesty additions):
    - BOTH bounds and confidences are stored as distinct labeled columns (R2).
    - probe_quality_json is augmented with scrub_counts (what surfaces were
      scrubbed), paraphrase_degraded flag (true when ANTHROPIC_API_KEY absent
      and paraphrase probes degrade to partial), and actual_distribution (the
      real probe counts executed, not the nominal 40/30/20/10).
    - restore_complete (H1 review fix) records whether a rejected/errored forget
      cleanly unwound. False means the graph is left inconsistent — the caller
      surfaces status 'forget-failed-partial-restore' in that case. The flag is
      a top-level key in probe_quality_json so a consumer reading the audit row
      has an honest signal without reaching into diagnostic_json.
    - The binomial bound conditions on probe sensitivity across scrubbed
      surfaces; if paraphrase_degraded=True the actual distribution differs
      from the nominal and this is recorded explicitly.
    """
    # Augment probe_quality with honesty fields (fblai-152r8 Half-C + review).
    probe_quality_full = dict(probe_quality)
    if scrub_counts is not None:
        probe_quality_full["scrub_counts"] = scrub_counts
    probe_quality_full["paraphrase_degraded"] = paraphrase_degraded
    if actual_distribution is not None:
        probe_quality_full["actual_distribution"] = actual_distribution
    probe_quality_full["restore_complete"] = restore_complete

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
                json.dumps(probe_quality_full),
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

    Single-thought version. For multi-thought callers (e.g. ``search()``
    scoring its result set), use :func:`compute_effective_weights_batch`
    which pushes the sum into Postgres in a single round-trip.
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


def compute_effective_weights_batch(
    conn,
    thought_ids: List[str],
    user_id: str,
) -> Dict[str, float]:
    """Batch version of :func:`compute_effective_weight` — one SQL round-trip.

    Pushes the time-decay sum into Postgres rather than walking rows in
    Python. Used by :func:`search` to score a full result set without
    issuing N separate queries.

    Formula (server-side, identical to the single-thought version)::

        effective_weight = SUM( weight * POWER(1 + days_since, -0.7) )

    Filters by ``user_id`` (PS scope) AND ``thought_id = ANY(:ids)``.
    Negative ``days_since`` (clock skew) is clamped to 0 in SQL — matching
    the single-thought implementation's behavior — so the per-row decay
    factor caps at 1.0.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    thought_ids
        List of thought_ids to score. Empty list → empty dict (no SQL).
    user_id
        Caller PS scope.

    Returns
    -------
    dict
        Mapping ``thought_id -> effective_weight``. Every input ``thought_id``
        appears in the result: thoughts with no matching promotions default
        to ``0.0`` rather than being silently dropped.
    """
    if not thought_ids:
        return {}
    cur = conn.cursor()
    try:
        # `ANY(%s)` accepts a Python list and binds as a Postgres array.
        # Cleaner than IN (%s, %s, ...) at variable arity.
        # Apply the same clock-skew clamp the single-thought version does
        # by wrapping (NOW() - promoted_at) days-since in GREATEST(., 0).
        cur.execute(
            """
            SELECT thought_id,
                   COALESCE(SUM(
                       weight * POWER(
                           1.0 + GREATEST(
                               0.0,
                               EXTRACT(EPOCH FROM (NOW() - promoted_at)) / 86400.0
                           ),
                           %s
                       )
                   ), 0.0) AS effective_weight
            FROM brain.promotions
            WHERE user_id = %s AND thought_id = ANY(%s)
            GROUP BY thought_id
            """,
            (HEBBIAN_DECAY_EXPONENT, user_id, list(thought_ids)),
        )
        by_tid = {row[0]: float(row[1]) for row in cur.fetchall()}
    finally:
        cur.close()
    # Thoughts with zero promotions don't appear in the GROUP BY result —
    # callers expect a complete mapping (e.g., search() iterates result
    # rows by tid), so default missing ids to 0.0.
    return {tid: by_tid.get(tid, 0.0) for tid in thought_ids}


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

    Notes
    -----
    **PS contract granularity (gz-ldmr4):** Principal Scoping is enforced
    at ``user_id`` granularity only. There is no ``project`` sub-scope —
    a ``was_derived_from`` parent in the same user's scope is accepted
    even if its ``project`` field differs from the caller's. The
    ``project`` column is metadata, not an isolation boundary. If you
    need stricter isolation (e.g., per-project), enforce it at the
    application layer or scope `user_id` to include a project suffix.
    """
    thought_id = _generate_thought_id()
    cur = conn.cursor()
    try:
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
                raise RuntimeError(
                    "was_derived_from references non-existent thought "
                    f"(or wrong user scope): {was_derived_from} (user={user_id})"
                )

        # ── fblai-y0zsb: Front-door redaction ────────────────────────────────
        # Redact PII/secrets BEFORE any text reaches an external LLM or the DB.
        #
        # OPEN_BRAIN_STORE_RAW=true  →  escape hatch: store raw text in the DB
        #   but STILL send only the redacted text to LLMs.  The atom is stamped
        #   metadata['stored_raw']=True so consumers know it was stored raw.
        #   A boot-WARN is emitted to stderr whenever the escape hatch is active.
        #
        # Default (env var absent or any value other than "true"):
        #   both the DB raw_text column and all LLM calls receive the redacted
        #   text.  No PII/secrets can leak to Neon or Anthropic/OpenAI/Ollama.
        #
        # Embedding note: the embedding is generated from the SAME text that is
        #   stored (redacted in the default path, raw when escape hatch is on),
        #   so the searchable vector representation always matches what is stored.
        #   Trade-off: in the default path, token-level embeddings are computed on
        #   the redacted text, meaning semantic neighbours of the redacted placeholder
        #   strings may differ slightly from neighbours of the original secret tokens.
        #   This is an acceptable loss — the alternative (embedding raw, storing
        #   redacted) would make search results inconsistent with stored content.
        store_raw = os.environ.get("OPEN_BRAIN_STORE_RAW", "").lower() == "true"
        if store_raw:
            import sys as _sys
            print(
                "WARNING: OPEN_BRAIN_STORE_RAW=true is active — "
                "raw (unredacted) text will be stored in brain.thoughts.raw_text. "
                "External LLMs still receive only redacted text. "
                "This is an escape hatch; disable in production. (fblai-y0zsb)",
                file=_sys.stderr,
            )

        redacted_text = redact_pii(text)
        # text_for_storage: raw when escape hatch is active, else redacted.
        text_for_storage = text if store_raw else redacted_text

        # Step 1: Extract metadata via Claude API — ALWAYS uses redacted text.
        metadata = _extract_metadata(redacted_text)
        if not metadata:
            metadata = {
                "type": "insight",
                "topics": [],
                "people": [],
                "action_items": [],
                "summary": redacted_text[:200],
            }

        # Stamp escape-hatch flag so consumers can distinguish raw-stored atoms.
        if store_raw:
            metadata["stored_raw"] = True

        thought_type = metadata.get("type", "insight")
        topics = metadata.get("topics", [])
        people = metadata.get("people", [])
        action_items = metadata.get("action_items", [])
        # gz-j29f0 — null-summary guard. `dict.get("summary", default)`
        # returns None when the LLM payload contains an explicit
        # {"summary": null} (vs a missing key, where the default applies).
        # The `or` idiom catches both None and the empty string, so
        # downstream `summary[:1000]` slicing never trips
        # `TypeError: 'NoneType' object is not subscriptable`.
        summary = metadata.get("summary") or redacted_text[:200]

        # Step 2: Generate embedding from text_for_storage so the vector
        # representation is consistent with what is stored in raw_text.
        embedding = _generate_embedding(text_for_storage)

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
                text_for_storage[:16384],
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
    except Exception:
        # gz-af9kn — cursor try-finally + rollback. _extract_metadata and
        # _generate_embedding both call external services (Claude API,
        # sentence-transformers); either can raise mid-capture. Without
        # this guard the cursor + tx state leak back to the caller.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass

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
        # Pass the stored representation, not the raw `text`. emit_replay_log
        # redacts internally so `text` would also be safe, but using
        # text_for_storage makes the no-raw-bypass intent unambiguous and
        # keeps this call consistent with what landed in raw_text. (fblai-y0zsb)
        result_text=text_for_storage,
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

    # brain-W1-S13 (gz-97l2z): Hebbian promotion boost, gated by within-kind
    # over-application defense. A heavily-promoted-but-irrelevant thought
    # MUST NOT outrank a relevant unpromoted one — so the boost is zeroed
    # below HEBBIAN_MIN_RELEVANCE_FLOOR (0.30 cosine sim). Defense per
    # gz-dsax2 / W1-R0 finding.
    #
    # Final score formula:
    #     hybrid_score' = hybrid_score
    #                     + HEBBIAN_BOOST_COEFFICIENT * effective_weight
    #                       if similarity >= HEBBIAN_MIN_RELEVANCE_FLOOR
    #                       else 0
    #
    # Implementation note: effective_weight fetched via single SQL aggregate
    # round-trip (compute_effective_weights_batch — gz-8nsvj) rather than
    # N per-thought queries.
    if results:
        candidate_tids = [r.get("THOUGHT_ID") for r in results if r.get("THOUGHT_ID")]
        if candidate_tids:
            try:
                weights_by_tid = compute_effective_weights_batch(
                    conn, candidate_tids, user_id,
                )
            except Exception:
                # On any aggregate error, degrade gracefully: emit zero
                # boosts. Hebbian is a scoring assist, not a correctness
                # invariant — search() must still return results.
                try:
                    conn.rollback()
                except Exception:
                    pass
                weights_by_tid = {tid: 0.0 for tid in candidate_tids}

            for r in results:
                tid = r.get("THOUGHT_ID")
                vs = float(r.get("SIMILARITY", 0.0) or 0.0)
                eff_weight = float(weights_by_tid.get(tid, 0.0))
                # Gate: below the floor, boost is 0 regardless of weight.
                if vs >= HEBBIAN_MIN_RELEVANCE_FLOOR and eff_weight != 0.0:
                    promotion_boost = HEBBIAN_BOOST_COEFFICIENT * eff_weight
                else:
                    promotion_boost = 0.0
                r["EFFECTIVE_WEIGHT"] = round(eff_weight, 4)
                r["PROMOTION_BOOST"] = round(promotion_boost, 4)
                base_score = float(r.get("HYBRID_SCORE", 0.0) or 0.0)
                r["HYBRID_SCORE"] = round(base_score + promotion_boost, 4)

            # Re-sort by the now-boosted HYBRID_SCORE — the input order
            # reflects pre-boost ranking. Only re-sort when sort_by leaves
            # similarity-driven ordering in effect (sort_by="time" callers
            # explicitly want chronological order; respect that).
            if sort_by != "time":
                results.sort(
                    key=lambda x: float(x.get("HYBRID_SCORE", 0.0) or 0.0),
                    reverse=True,
                )

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

    # brain-W2-S6.1 (gz-woema): replay log emission for search ops.
    # Query is PII-redacted at the emitter boundary. result_summary captures
    # the top result's preview + total count so audit trails are informative
    # without holding the full per-row payload.
    top_thought_id = results[0].get("thought_id") if results else None
    top_similarity = results[0].get("similarity") if results else None
    result_text_preview = (
        f"{len(results)} result(s); top={results[0].get('summary', '')[:80]}"
        if results
        else "0 results"
    )
    emit_replay_log(
        conn,
        user_id=user_id,
        event_type="search",
        query=query,
        result_text=result_text_preview,
        metadata={
            "result_count": len(results),
            "top_thought_id": top_thought_id,
            "top_similarity": top_similarity,
            "limit": limit,
            "threshold": threshold,
            "sort_by": sort_by,
            "has_filters": any([thought_type, topics, people, date_from, date_to]),
        },
    )

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


# ─── Connected provenance graph: atom_links operations (gz-0l68v) ────────────
#
# Typed many-to-many links between atoms. Orthogonal to the was_derived_from
# PROV-DM column on brain.thoughts — that column stays as PROV-DM scaffolding,
# atom_links is the typed-graph layer on top.
#
# Targets can be:
#  - another atom thought_id (e.g. "brain-1780497129-1225dd48")
#  - a bead id (e.g. "gz-0l68v")
#  - anything else (queried later via --query-orphan-links for data-integrity audit)
#
# Source FK has ON DELETE CASCADE on brain.thoughts(thought_id), so when an
# atom is VF_eps-forgotten its outgoing links go with it. Inbound links to a
# forgotten atom become orphans which --query-orphan-links exposes.


def _parse_link_spec(spec: str) -> tuple:
    """Parse a ``target_id:link_type`` capture-flag spec into a tuple.

    Raises ``ValueError`` with a clear message on:
      - empty/whitespace input
      - missing ``:`` separator
      - empty target_id or link_type
      - link_type not in ``LINK_TYPES``

    Returns
    -------
    tuple[str, str]
        ``(target_id, link_type)`` — both stripped of whitespace.
    """
    if not spec or not spec.strip():
        raise ValueError("link spec is empty")
    s = spec.strip()
    if ":" not in s:
        raise ValueError(
            f"link spec must be '<target_id>:<link_type>' "
            f"(missing ':' in {s!r})"
        )
    # rsplit so a target_id that contains ':' (defensive — none should) gets
    # the link_type as the LAST colon-delimited token.
    target_id, link_type = s.rsplit(":", 1)
    target_id = target_id.strip()
    link_type = link_type.strip()
    if not target_id:
        raise ValueError(f"link spec has empty target_id: {spec!r}")
    if not link_type:
        raise ValueError(f"link spec has empty link_type: {spec!r}")
    if link_type not in LINK_TYPES:
        allowed = ", ".join(sorted(LINK_TYPES))
        raise ValueError(
            f"unknown link_type {link_type!r}; allowed: {allowed}"
        )
    return target_id, link_type


def _build_link_prov(
    prov_agent: Optional[str],
    via: str,
    user_id: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the per-link PROV-DM stamp written to ``atom_links.prov``.

    Parameters
    ----------
    prov_agent
        Override for the agent identifier. When ``None``, falls back to
        ``claude-code``.
    via
        Either ``"capture-flag"`` (link emitted by ``--capture --link ...``)
        or ``"post-hoc"`` (link emitted by ``--add-link``). Other values
        accepted for future extensions.
    user_id
        Tenant / principal — recorded under ``prov`` for trace walks even
        though it's already the row's ``user_id`` column.
    session_id
        Optional. Falls back to ``$CLAUDE_CODE_SESSION_ID`` env var when
        not explicitly provided.
    """
    agent = prov_agent if prov_agent else "claude-code"
    sess = session_id if session_id else os.environ.get("CLAUDE_CODE_SESSION_ID")
    stamp: Dict[str, Any] = {
        "agent": agent,
        "activity": "link_add",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "via": via,
        "user_id": user_id,
    }
    if sess:
        stamp["session_id"] = sess
    return stamp


def _classify_target_kind(conn, target_id: str, user_id: str) -> str:
    """Classify a link target as ``"atom"``, ``"bead"``, or ``"unknown"``.

    Rules:
      - If ``target_id`` exists in ``brain.thoughts`` AND belongs to ``user_id``,
        return ``"atom"``.
      - Otherwise, if ``target_id`` starts with ``"gz-"``, return ``"bead"``.
      - Otherwise return ``"unknown"``.

    Note the order: an atom-id that LOOKS like a bead (it doesn't — atoms
    start with ``brain-``) would still be checked as an atom first; but in
    practice atom IDs and bead IDs have disjoint prefixes so this is just
    defense-in-depth.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
            (target_id, user_id),
        )
        if cur.fetchone() is not None:
            return "atom"
    finally:
        cur.close()
    if target_id.startswith(("gz-", "fblai-", "optivai-")):
        return "bead"
    return "unknown"


def add_link(
    conn,
    source_id: str,
    target_id: str,
    link_type: str,
    user_id: str,
    via: str = "post-hoc",
    prov_agent: Optional[str] = None,
    session_id: Optional[str] = None,
    verify_source_exists: bool = True,
) -> Dict[str, Any]:
    """Insert a row into ``brain.atom_links``.

    Validates:
      - ``link_type`` is in ``LINK_TYPES`` (ValueError if not).
      - ``source_id`` exists in ``brain.thoughts`` under ``user_id`` scope
        (RuntimeError if not, AND ``verify_source_exists=True``).

    Uses ``ON CONFLICT DO NOTHING`` on the
    ``(source_id, target_id, link_type, user_id)`` unique constraint:
    re-inserting the same edge is an idempotent no-op (returns the
    existing ``link_id`` rather than raising).

    Returns
    -------
    dict
        ``{"link_id": int, "source_id": str, "target_id": str,
           "link_type": str, "created": bool}`` where ``created`` is
        ``True`` for a fresh insert and ``False`` for an existing edge.
    """
    if link_type not in LINK_TYPES:
        allowed = ", ".join(sorted(LINK_TYPES))
        raise ValueError(
            f"unknown link_type {link_type!r}; allowed: {allowed}"
        )
    if not source_id or not source_id.strip():
        raise ValueError("source_id is empty")
    if not target_id or not target_id.strip():
        raise ValueError("target_id is empty")

    source_id = source_id.strip()
    target_id = target_id.strip()

    cur = conn.cursor()
    try:
        if verify_source_exists:
            cur.execute(
                "SELECT 1 FROM brain.thoughts WHERE thought_id = %s AND user_id = %s",
                (source_id, user_id),
            )
            if cur.fetchone() is None:
                raise RuntimeError(
                    f"add_link: source atom {source_id!r} does not exist "
                    f"in user scope (user={user_id})"
                )

        prov = _build_link_prov(
            prov_agent=prov_agent,
            via=via,
            user_id=user_id,
            session_id=session_id,
        )
        cur.execute(
            """
            INSERT INTO brain.atom_links
                (source_id, target_id, link_type, prov, user_id)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT ON CONSTRAINT atom_links_unique DO NOTHING
            RETURNING link_id
            """,
            (source_id, target_id, link_type, json.dumps(prov), user_id),
        )
        row = cur.fetchone()
        created = row is not None
        if created:
            link_id = row[0]
        else:
            # Re-fetch existing row's link_id for the idempotent path.
            cur.execute(
                """
                SELECT link_id FROM brain.atom_links
                WHERE source_id = %s AND target_id = %s
                  AND link_type = %s AND user_id = %s
                """,
                (source_id, target_id, link_type, user_id),
            )
            row2 = cur.fetchone()
            link_id = row2[0] if row2 else -1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()

    # Always emit the confirmation line per the fail-safe contract, even on
    # the idempotent no-op path (caller still benefits from seeing the link_id).
    sys.stderr.write(
        f"Link added: source={source_id} type={link_type} "
        f"target={target_id} link_id={link_id}\n"
    )
    return {
        "link_id": link_id,
        "source_id": source_id,
        "target_id": target_id,
        "link_type": link_type,
        "created": created,
    }


def show_links(conn, atom_id: str, user_id: str) -> Dict[str, Any]:
    """List outgoing AND incoming links for an atom.

    Outgoing links are rows where ``source_id == atom_id``; incoming links
    are rows where ``target_id == atom_id``. Each link gets a derived
    ``target_kind`` (for outgoing) or ``source_kind`` (for incoming),
    classified at query time via :func:`_classify_target_kind`.

    Returns
    -------
    dict
        ``{"atom_id": str, "outgoing": [...], "incoming": [...]}``
    """
    if not atom_id or not atom_id.strip():
        raise ValueError("atom_id is empty")
    atom_id = atom_id.strip()
    cur = conn.cursor()
    try:
        # Outgoing
        cur.execute(
            """
            SELECT link_id, source_id, target_id, link_type, prov, created_at
            FROM brain.atom_links
            WHERE source_id = %s AND user_id = %s
            ORDER BY created_at ASC, link_id ASC
            """,
            (atom_id, user_id),
        )
        out_rows = cur.fetchall()
        # Incoming
        cur.execute(
            """
            SELECT link_id, source_id, target_id, link_type, prov, created_at
            FROM brain.atom_links
            WHERE target_id = %s AND user_id = %s
            ORDER BY created_at ASC, link_id ASC
            """,
            (atom_id, user_id),
        )
        in_rows = cur.fetchall()
    finally:
        cur.close()

    def _row_to_dict(r, kind_field: str, classify_id: str) -> Dict[str, Any]:
        prov = r[4]
        # psycopg2 returns jsonb as either dict or str depending on the
        # column type registration. Normalize to dict for JSON-serializable
        # output without double-encoding.
        if isinstance(prov, str):
            try:
                prov = json.loads(prov)
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "link_id": r[0],
            "source_id": r[1],
            "target_id": r[2],
            "link_type": r[3],
            "prov": prov,
            "created_at": str(r[5]) if r[5] else "",
            kind_field: _classify_target_kind(conn, classify_id, user_id),
        }

    outgoing = [_row_to_dict(r, "target_kind", r[2]) for r in out_rows]
    incoming = [_row_to_dict(r, "source_kind", r[1]) for r in in_rows]

    return {
        "atom_id": atom_id,
        "outgoing": outgoing,
        "incoming": incoming,
    }


_UNRESOLVED_KEYWORD_RE = re.compile(
    r"\b(finding|bug|issue|defect|gap|regression|problem)\b",
    re.IGNORECASE,
)


def query_unresolved_findings(
    conn,
    user_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return atoms that look like unresolved findings.

    An atom is considered an unresolved finding when ALL of the following hold:
      1. Its ``user_id`` matches the caller.
      2. EITHER ``thought_type = 'sentinel_relevant'`` OR the metadata
         contains a Pearl ``kind = 'fact'``.
      3. The ``summary`` or ``raw_text`` matches the finding/bug/issue
         keyword regex.
      4. NO atom_links row exists with ``target_id = atom.thought_id``
         AND ``link_type = 'resolves'`` (under the caller's user_id).

    The keyword filter is applied in Python after a SQL pre-filter (to
    keep the SQL conservative and portable). Caps at ``limit`` results
    (default 50).

    Returns
    -------
    list of dict
        Each dict carries ``thought_id``, ``thought_type``, ``summary``,
        ``raw_text`` (first 500 chars), ``created_at``.
    """
    if limit <= 0:
        limit = 50
    cur = conn.cursor()
    try:
        # Conservative SQL pre-filter: ANY type that COULD be a finding +
        # absence of any incoming resolves link. The keyword check is
        # applied in Python.
        cur.execute(
            """
            SELECT t.thought_id, t.thought_type, t.summary, t.raw_text,
                   t.created_at, t.metadata
            FROM brain.thoughts t
            WHERE t.user_id = %s
              AND (
                    t.thought_type = 'sentinel_relevant'
                    OR (t.metadata IS NOT NULL
                        AND t.metadata::jsonb -> 'kind' = '"fact"'::jsonb)
              )
              AND NOT EXISTS (
                SELECT 1 FROM brain.atom_links l
                WHERE l.target_id = t.thought_id
                  AND l.link_type = 'resolves'
                  AND l.user_id = %s
              )
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (user_id, user_id, max(limit * 4, limit)),  # over-fetch to allow keyword filter
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        summary = r[2] or ""
        raw = r[3] or ""
        if not (_UNRESOLVED_KEYWORD_RE.search(summary)
                or _UNRESOLVED_KEYWORD_RE.search(raw)):
            continue
        out.append({
            "thought_id": r[0],
            "thought_type": r[1],
            "summary": summary,
            "raw_text": raw[:500],
            "created_at": str(r[4]) if r[4] else "",
        })
        if len(out) >= limit:
            break
    return out


def query_orphan_links(
    conn,
    user_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return links whose target_id is genuinely dangling.

    A link is an orphan when ALL hold:
      - target_id is NOT in ``brain.thoughts`` (under the caller's user_id);
      - target_id does NOT match the ``gz-`` bead prefix.

    Returns
    -------
    list of dict
        Each dict carries ``link_id``, ``source_id``, ``target_id``,
        ``link_type``, ``created_at``.
    """
    if limit <= 0:
        limit = 100
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT l.link_id, l.source_id, l.target_id, l.link_type, l.created_at
            FROM brain.atom_links l
            WHERE l.user_id = %s
              AND l.target_id NOT LIKE 'gz-%%'
              AND NOT EXISTS (
                SELECT 1 FROM brain.thoughts t
                WHERE t.thought_id = l.target_id AND t.user_id = %s
              )
            ORDER BY l.created_at DESC, l.link_id DESC
            LIMIT %s
            """,
            (user_id, user_id, limit),
        )
        rows = cur.fetchall()
    finally:
        cur.close()
    return [
        {
            "link_id": r[0],
            "source_id": r[1],
            "target_id": r[2],
            "link_type": r[3],
            "created_at": str(r[4]) if r[4] else "",
        }
        for r in rows
    ]


# ─── Skill registration: composite capture + Hebbian +2.0 + derives_from ─────
#
# gz-nced6 — Enhancement #4 of the brain-substrate stream. The principle is
# that *capture is the registration* but Hebbian promotion is what tells
# future recall this is reusable knowledge worth surfacing. Today both are
# manual + separate; --register-skill wires them together as ONE primitive so
# the agent learns by registering instead of just capturing.
#
# Composition (atomic from the user's point of view):
#   1. capture an atom with prov_activity='skill_register' + a SKILL header
#   2. overwrite thought_type to 'skill_ref' + inject metadata.pearl_kind +
#      metadata.skill_name (the LLM metadata extractor will classify the body
#      as 'pattern' or 'insight' — we authoritatively override after the row
#      is written, in the same connection, before promote/links fire)
#   3. promote_thought(weight=2.0) — the "this is reusable knowledge" signal
#      (double the standard +1.0 promote)
#   4. add_link(link_type='derives_from') for each --from-pattern target
#
# Pre-validations (NOTHING is written if any fails):
#   * name format: ^[a-z0-9][a-z0-9_-]{2,63}$
#   * description length: 10–4000 chars (matches the v0.19 Level-B convention)
#   * each from-pattern atom_id must exist in brain.thoughts under user_id
#
# Soft-warn (does NOT block):
#   * duplicate skill name (same name + thought_type='skill_ref' + user_id) —
#     stderr warning lists the existing skill_id; the user can rebuild or
#     update post-hoc. We do not block because re-registering a skill is a
#     legitimate intent (re-validation by Rule 4 promotion semantics).
#
# Returns a single dict shaped for both human and JSON output:
#   {"skill_id": "...", "name": "...", "promoted_weight": 2.0,
#    "linked_from_patterns": [...]}


SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
SKILL_DESCRIPTION_MIN = 10
SKILL_DESCRIPTION_MAX = 4000
SKILL_PROMOTE_WEIGHT = 2.0  # double the standard +1.0 promote (reusable-knowledge signal)


def _validate_skill_name(name: str) -> None:
    """Raise ``ValueError`` if ``name`` does not match the canonical skill name
    format ``^[a-z0-9][a-z0-9_-]{2,63}$`` (3–64 chars, lowercase
    kebab/snake, starting with alphanumeric).
    """
    if not isinstance(name, str) or not SKILL_NAME_RE.match(name):
        raise ValueError(
            f"invalid skill name {name!r}: must match "
            f"^[a-z0-9][a-z0-9_-]{{2,63}}$ (3–64 chars, lowercase, "
            f"kebab/snake, starting alnum)"
        )


def _validate_skill_description(description: str) -> None:
    """Raise ``ValueError`` if ``description`` is outside
    ``[SKILL_DESCRIPTION_MIN, SKILL_DESCRIPTION_MAX]`` chars after stripping
    surrounding whitespace.
    """
    if not isinstance(description, str):
        raise ValueError("skill description must be a string")
    stripped = description.strip()
    if len(stripped) < SKILL_DESCRIPTION_MIN:
        raise ValueError(
            f"skill description too short ({len(stripped)} chars); "
            f"minimum {SKILL_DESCRIPTION_MIN}"
        )
    if len(stripped) > SKILL_DESCRIPTION_MAX:
        raise ValueError(
            f"skill description too long ({len(stripped)} chars); "
            f"maximum {SKILL_DESCRIPTION_MAX}"
        )


def _find_existing_skill_by_name(
    conn, user_id: str, name: str,
) -> Optional[str]:
    """Return the thought_id of an existing skill with the same canonical name,
    or ``None`` if no such skill exists in the user's scope.

    The match is exact on ``metadata->>'skill_name'`` (the authoritative
    field, written by :func:`register_skill`). Both the skill name and the
    raw_text SKILL: prefix are checked — the latter as defense-in-depth in
    case an external INSERT bypassed the metadata stamp.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT thought_id
            FROM brain.thoughts
            WHERE user_id = %s
              AND thought_type = 'skill_ref'
              AND (
                metadata->>'skill_name' = %s
                OR raw_text LIKE %s
              )
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, name, f"SKILL: {name}\n%"),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _stamp_skill_metadata(
    conn, thought_id: str, user_id: str, name: str,
) -> None:
    """Force ``thought_type='skill_ref'`` and merge
    ``metadata.pearl_kind='skill_ref'`` + ``metadata.skill_name=name`` onto
    the just-captured atom.

    The LLM metadata extractor classifies the body autonomously — it will
    typically return ``pattern`` or ``insight``. The composite
    ``register_skill`` primitive's contract demands the row carry
    ``skill_ref`` as the authoritative kind in BOTH the column (for index
    scans) AND the metadata JSONB (for Pearl-aligned readers). This helper
    is called from :func:`register_skill` immediately after :func:`capture`
    returns, in the same connection, before promote or links fire — so the
    composite operation is atomic from the user's point of view.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE brain.thoughts
            SET thought_type = 'skill_ref',
                metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object(
                                'pearl_kind', 'skill_ref',
                                'skill_name', %s::text
                              ),
                updated_at = NOW()
            WHERE thought_id = %s AND user_id = %s
            """,
            (name, thought_id, user_id),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()


def register_skill(
    conn,
    name: str,
    description: str,
    user_id: str,
    from_patterns: Optional[List[str]] = None,
    prov_agent: Optional[str] = None,
    project: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Composite primitive: capture + Hebbian +2.0 promote + derives_from links.

    Atomic from the user's point of view, but implemented as a sequence
    so each substep can be tested independently and so a downstream failure
    does not silently leave half-written state.

    Sequence (in this order; ALL pre-validations run BEFORE any DB write):
      1. validate ``name`` format (``^[a-z0-9][a-z0-9_-]{2,63}$``)
      2. validate ``description`` length (10–4000 chars)
      3. validate every ``from_patterns`` atom exists in user scope
      4. soft-warn (stderr) on duplicate name
      5. capture(...) the atom with ``prov_activity='skill_register'``
      6. overwrite ``thought_type='skill_ref'`` + inject metadata
      7. promote_thought(weight=+2.0)
      8. add_link(link_type='derives_from') for each from-pattern

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    name
        Canonical skill name (lowercase kebab/snake, 3–64 chars).
    description
        Free-form description (10–4000 chars).
    user_id
        Caller PS scope.
    from_patterns
        Optional list of atom thought_ids this skill generalizes. Each
        is written as a ``derives_from`` link from the new skill atom to
        the named pattern. Every id is verified to exist in the caller's
        scope BEFORE any write — a missing id rejects the whole register
        (NOT silently skipped — we don't want orphan claims of derivation).
    prov_agent
        Override the default agent identifier. Defaults to
        ``cli-user-{user_id}`` per :func:`_derive_prov_agent`.
    project, session_id
        Forwarded to :func:`capture`.

    Returns
    -------
    dict
        ``{"skill_id": str, "name": str, "promoted_weight": float,
           "linked_from_patterns": [str, ...]}``

    Raises
    ------
    ValueError
        Invalid name format or description length.
    RuntimeError
        Any from-pattern atom_id does not exist in the user's scope.
    """
    # ─── Pre-validations (no DB writes) ──────────────────────────────────
    _validate_skill_name(name)
    _validate_skill_description(description)

    from_patterns = list(from_patterns or [])
    # Strip + dedupe while preserving order — repeated --from-pattern X X X
    # is degenerate but should not double-write the same link.
    seen: set = set()
    cleaned_patterns: List[str] = []
    for fp in from_patterns:
        if fp is None:
            continue
        fp_s = fp.strip()
        if not fp_s:
            continue
        if fp_s in seen:
            continue
        seen.add(fp_s)
        cleaned_patterns.append(fp_s)
    from_patterns = cleaned_patterns

    # Verify every from-pattern target exists in user scope BEFORE any write.
    if from_patterns:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT thought_id FROM brain.thoughts
                WHERE user_id = %s AND thought_id = ANY(%s)
                """,
                (user_id, from_patterns),
            )
            existing = {row[0] for row in cur.fetchall()}
        finally:
            cur.close()
        missing = [fp for fp in from_patterns if fp not in existing]
        if missing:
            raise RuntimeError(
                f"register_skill: from-pattern atom(s) not in user scope "
                f"(user={user_id}): {missing}"
            )

    # Duplicate-name soft-warn (does NOT block). Look up an existing skill
    # by canonical name in the user's scope and surface the existing id.
    existing_skill_id = _find_existing_skill_by_name(conn, user_id, name)
    if existing_skill_id is not None:
        sys.stderr.write(
            f"Warning: a skill named {name!r} already exists "
            f"(id={existing_skill_id}) — capturing anyway; consider "
            f"--update-skill if you meant to replace.\n"
        )

    # ─── Composite write ─────────────────────────────────────────────────
    # Redact PII from the description before composing the body so PII in
    # skill descriptions never reaches the row. Fail-open: if redact_pii
    # raises for any reason, fall back to the original text — the
    # capture-path redactor (replay-log emitter) is still in play.
    try:
        redacted_description = redact_pii(description) or description
    except Exception:
        redacted_description = description

    body = f"SKILL: {name}\n\n{redacted_description.strip()}"

    # capture() handles embedding generation + LLM metadata extraction +
    # PROV-DM stamping + replay-log emission. We pass prov_activity to
    # mark this as a skill_register event (overrides the default 'capture').
    cap = capture(
        conn,
        text=body,
        user_id=user_id,
        source="manual",
        session_id=session_id,
        project=project,
        prov_agent=prov_agent,
        prov_activity="skill_register",
    )
    skill_id = cap["thought_id"]

    # Force thought_type='skill_ref' + inject pearl_kind + skill_name.
    # The LLM metadata extractor will have classified the body as
    # 'pattern' or 'insight' — we override authoritatively here so the
    # column AND the metadata blob both carry 'skill_ref'.
    _stamp_skill_metadata(conn, skill_id, user_id, name)

    # Hebbian +2.0 promote — the "this is reusable knowledge" signal so
    # future recall ranks it higher than a one-off pattern.
    promote_thought(
        conn,
        thought_id=skill_id,
        user_id=user_id,
        weight=SKILL_PROMOTE_WEIGHT,
        reason=f"skill registration: {name}",
        prov_agent=prov_agent,
    )

    # derives_from links to each source pattern. Verify-source-exists is
    # skipped (we just inserted the skill atom in this same connection).
    linked: List[str] = []
    for target_id in from_patterns:
        add_link(
            conn,
            source_id=skill_id,
            target_id=target_id,
            link_type="derives_from",
            user_id=user_id,
            via="skill-register",
            prov_agent=prov_agent,
            session_id=session_id or None,
            verify_source_exists=False,
        )
        linked.append(target_id)

    return {
        "skill_id": skill_id,
        "name": name,
        "promoted_weight": float(SKILL_PROMOTE_WEIGHT),
        "linked_from_patterns": linked,
    }


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
    # Security: always derive user_id from the OS principal; never honor a
    # caller-supplied value — accepting it would allow any process writing to
    # the stdin bridge to impersonate an arbitrary user (principal-scoping bypass).
    if "user_id" in args:
        print(
            "WARNING: ignoring caller-supplied user_id; deriving from OS principal",
            file=sys.stderr,
        )
    user_id = _get_user_id()

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
            # Security: admin_stats returns ALL users' data with no user predicate.
            # Gate it behind an explicit server-side env flag so a caller cannot
            # enumerate cross-user statistics through the stdin bridge.
            if os.environ.get("OPEN_BRAIN_ALLOW_ADMIN", "").lower() != "true":
                print(json.dumps({
                    "error": "permission denied: admin_stats requires OPEN_BRAIN_ALLOW_ADMIN=true on the server process"
                }))
            else:
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
        # ─── Wave-1+2 ops (brain-W2-D2 stdin-JSON dispatch) ──────────────────
        elif op == "forget":
            result = forget_thought(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                epsilon=args.get("epsilon", 0.05),
                n=args.get("n", 300),
                prov_agent=args.get("prov_agent"),
            )
            print(json.dumps(result, default=str))
        elif op == "snapshot":
            result = snapshot_thought(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                prov_agent=args.get("prov_agent"),
                prov_activity=args.get("prov_activity", "snapshot"),
            )
            print(json.dumps(result, default=str))
        elif op == "versions":
            result = list_versions(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
            )
            print(json.dumps(result, default=str))
        elif op == "rollback":
            result = rollback_thought(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                to_revision=args.get("to_revision", 1),
                prov_agent=args.get("prov_agent"),
            )
            print(json.dumps(result, default=str))
        elif op == "diff":
            result = diff_versions(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                revision_a=args.get("revision_a", args.get("from_revision", 1)),
                revision_b=args.get("revision_b", args.get("to_revision", 2)),
            )
            print(json.dumps(result, default=str))
        elif op == "trace":
            import citation_walker
            root = citation_walker.trace_citation(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                max_depth=args.get("max_depth", 50),
            )
            print(json.dumps(citation_walker.citation_node_to_dict(root), default=str))
        elif op == "inspect":
            import time_travel
            at_iso = args.get("at")
            at_revision = args.get("at_revision")
            if at_revision is not None:
                result = time_travel.inspect_at_revision(
                    conn, args.get("thought_id", ""), user_id, revision=at_revision,
                )
            elif at_iso:
                result = time_travel.inspect_at_timestamp(
                    conn, args.get("thought_id", ""), user_id, at_iso=at_iso,
                )
            else:
                result = time_travel.inspect_latest(
                    conn, args.get("thought_id", ""), user_id,
                )
            if result is None:
                print(json.dumps({"thought_id": args.get("thought_id", ""), "result": None}))
            else:
                print(json.dumps(time_travel.inspect_result_to_dict(result), default=str))
        elif op == "promote":
            result = promote_thought(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                weight=args.get("weight", 1.0),
                reason=args.get("reason"),
                prov_agent=args.get("prov_agent"),
            )
            print(json.dumps(result, default=str))
        elif op == "demote":
            result = demote_thought(
                conn,
                thought_id=args.get("thought_id", ""),
                user_id=user_id,
                weight=args.get("weight", 1.0),
                reason=args.get("reason"),
                prov_agent=args.get("prov_agent"),
            )
            print(json.dumps(result, default=str))
        elif op == "replay":
            result = query_replay_log(
                conn,
                user_id=user_id,
                session_id=args.get("session_id"),
                from_iso=args.get("from_iso") or args.get("from"),
                to_iso=args.get("to_iso") or args.get("to"),
                event_type=args.get("event_type"),
                limit=args.get("limit", 100),
            )
            print(json.dumps(result, default=str))
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

    Security: the resolved canonical path of *sql_path* must be inside the
    repo's ``sql/`` directory.  ``Path.resolve()`` is called before the check
    so that symlinks pointing outside ``sql/`` are also rejected (symlink
    traversal protection).

    Returns a status dict suitable for JSON serialization. Raises RuntimeError
    (the local error convention in this module) on failure.
    """
    if not sql_path:
        raise RuntimeError("Migration path required")

    # Confinement: resolve both paths to their canonical form (follows symlinks)
    # so that ../traversal and symlink attacks are neutralised before the check.
    allowed_dir = (Path(__file__).resolve().parent.parent / "sql").resolve()
    try:
        resolved = Path(sql_path).resolve()
    except Exception as e:
        raise RuntimeError(f"Cannot resolve migration path: {sql_path!r}: {e}") from e

    try:
        resolved.relative_to(allowed_dir)
    except ValueError:
        raise RuntimeError(
            f"Migration path not allowed: {sql_path!r} resolves to {resolved!r}, "
            f"which is outside the confined sql/ directory ({allowed_dir!r})"
        )

    if not resolved.exists():
        raise RuntimeError(f"Migration file not found: {sql_path}")
    with open(resolved, "r", encoding="utf-8") as f:
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
    group.add_argument("--redact-test", type=str, metavar="TEXT", dest="redact_test",
                       help="Run the redaction pipeline against TEXT and show what gets caught")

    # ─── Connected provenance graph (gz-0l68v) ───────────────────────────────
    group.add_argument("--add-link", action="store_true", dest="add_link_op",
                       help="Add a typed link between atoms post-hoc "
                            "(combine with --source-id, --target-id, --link-type)")
    group.add_argument("--show-links", type=str, metavar="ATOM_ID",
                       dest="show_links_atom",
                       help="List outgoing and incoming typed links for an atom")
    group.add_argument("--query-unresolved-findings", action="store_true",
                       dest="query_unresolved",
                       help="List finding-like atoms with no incoming 'resolves' link "
                            "(combine with --limit; default 50)")
    group.add_argument("--query-orphan-links", action="store_true",
                       dest="query_orphans",
                       help="List atom_links whose target_id is genuinely dangling "
                            "(not an atom, not a bead). For data-integrity audit.")

    # ─── Skill registration (gz-nced6) ───────────────────────────────────────
    group.add_argument("--register-skill", type=str, metavar="NAME",
                       dest="register_skill_name",
                       help="Composite primitive: capture an atom with "
                            "thought_type='skill_ref', promote it via Hebbian "
                            "+2.0, and add derives_from links to each "
                            "--from-pattern. Requires --skill-description.")

    parser.add_argument("--skill-description", type=str, default=None,
                        dest="skill_description", metavar="TEXT",
                        help="Required with --register-skill. "
                             "Free-form description (10–4000 chars).")
    parser.add_argument("--from-pattern", action="append", default=[],
                        dest="from_patterns", metavar="ATOM_ID",
                        help="With --register-skill: thought_id of a pattern "
                             "this skill generalizes. Repeatable. Each id is "
                             "validated to exist in the user's scope before "
                             "any write.")

    # Operands for the link commands
    parser.add_argument("--source-id", type=str, default=None, dest="source_id",
                        metavar="ATOM_ID",
                        help="Source atom thought_id for --add-link")
    parser.add_argument("--target-id", type=str, default=None, dest="target_id",
                        metavar="ID",
                        help="Target id for --add-link (atom thought_id OR bead id like gz-XXXXX)")
    parser.add_argument("--link-type", type=str, default=None, dest="link_type",
                        metavar="TYPE",
                        help="Link type for --add-link (one of: "
                             + ", ".join(sorted(LINK_TYPES)) + ")")
    parser.add_argument("--link", action="append", default=[], dest="capture_links",
                        metavar="TARGET_ID:LINK_TYPE",
                        help="With --capture: add a typed link from the new atom to "
                             "TARGET_ID. Repeatable for multiple links. Validated before "
                             "the capture commits — an unknown link_type rejects the whole "
                             "capture.")

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

    if args.redact_test:
        # redact-S10: manual verification CLI. Runs the composed redaction
        # pipeline against TEXT and shows what gets caught. Useful for
        # partner-trust verification ("paste a sample, see what would be
        # redacted"). No DB connection needed.
        redacted = redact_pii(args.redact_test)
        if args.json:
            print(json.dumps({
                "input": args.redact_test,
                "redacted": redacted,
                "changed": args.redact_test != redacted,
            }, indent=2))
        else:
            print(f"Input:    {args.redact_test}")
            print(f"Redacted: {redacted}")
            if args.redact_test == redacted:
                print("(no changes — nothing caught)")
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
            # gz-0l68v — pre-validate every --link spec BEFORE the capture
            # commits, so an unknown link_type rejects the whole capture
            # (no half-written atom + half-written links).
            link_specs: List[tuple] = []
            for raw in (args.capture_links or []):
                try:
                    link_specs.append(_parse_link_spec(raw))
                except ValueError as e:
                    msg = f"--link rejected: {e}"
                    if args.json:
                        print(json.dumps({"error": msg, "spec": raw}))
                    else:
                        print(f"Error: {msg}", file=sys.stderr)
                    sys.exit(2)

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

            # Write the validated links AFTER capture commits. Each row
            # references the new atom's thought_id as source. We skip the
            # `verify_source_exists` recheck here because we KNOW the atom
            # was just inserted in the same connection.
            written_links: List[Dict[str, Any]] = []
            for target_id, link_type in link_specs:
                try:
                    lr = add_link(
                        conn,
                        source_id=result["thought_id"],
                        target_id=target_id,
                        link_type=link_type,
                        user_id=user_id,
                        via="capture-flag",
                        prov_agent=args.prov_agent,
                        session_id=args.session_id or None,
                        verify_source_exists=False,
                    )
                    written_links.append(lr)
                except Exception as e:
                    # Link write failure is logged but does not roll back
                    # the capture — the atom is durable, links are best-effort.
                    sys.stderr.write(
                        f"Link write failed (continuing): "
                        f"source={result['thought_id']} target={target_id} "
                        f"type={link_type} error={e}\n"
                    )

            if written_links:
                result["links"] = written_links

            if args.json:
                print(json.dumps(result))
            else:
                print(_format_capture_result(result))
                for lr in written_links:
                    print(
                        f"   Link: {lr['link_type']} -> {lr['target_id']} "
                        f"(link_id={lr['link_id']})"
                    )

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
                        f"confidence (tight; headline)"
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

        # ─── Connected provenance graph (gz-0l68v) ───────────────────────────
        elif args.add_link_op:
            missing = [
                name for name, val in
                [("--source-id", args.source_id),
                 ("--target-id", args.target_id),
                 ("--link-type", args.link_type)]
                if not val
            ]
            if missing:
                msg = f"--add-link requires {', '.join(missing)}"
                if args.json:
                    print(json.dumps({"error": msg}))
                else:
                    print(f"Error: {msg}", file=sys.stderr)
                sys.exit(2)
            try:
                result = add_link(
                    conn,
                    source_id=args.source_id,
                    target_id=args.target_id,
                    link_type=args.link_type,
                    user_id=user_id,
                    via="post-hoc",
                    prov_agent=args.prov_agent,
                    session_id=args.session_id or None,
                )
            except (ValueError, RuntimeError) as e:
                if args.json:
                    print(json.dumps({"error": str(e)}))
                else:
                    print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            if args.json:
                print(json.dumps(result, default=str))
            else:
                verb = "Created" if result.get("created") else "Existed"
                print(
                    f"{verb}: link_id={result['link_id']} "
                    f"source={result['source_id']} "
                    f"type={result['link_type']} "
                    f"target={result['target_id']}"
                )

        elif args.show_links_atom:
            result = show_links(
                conn,
                atom_id=args.show_links_atom,
                user_id=user_id,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Links for {result['atom_id']}:")
                print(f"  Outgoing ({len(result['outgoing'])}):")
                for lr in result["outgoing"]:
                    print(
                        f"    -> {lr['link_type']:24s} -> "
                        f"{lr['target_id']:42s} [{lr['target_kind']}] "
                        f"link_id={lr['link_id']}"
                    )
                print(f"  Incoming ({len(result['incoming'])}):")
                for lr in result["incoming"]:
                    print(
                        f"    <- {lr['link_type']:24s} <- "
                        f"{lr['source_id']:42s} [{lr['source_kind']}] "
                        f"link_id={lr['link_id']}"
                    )

        elif args.query_unresolved:
            limit = args.limit if args.limit and args.limit > 0 else 50
            rows = query_unresolved_findings(
                conn,
                user_id=user_id,
                limit=limit,
            )
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                if not rows:
                    print("No unresolved findings.")
                else:
                    print(f"Unresolved findings ({len(rows)} of max {limit}):")
                    for r in rows:
                        print(
                            f"  • {r['thought_id']} "
                            f"({r['thought_type']}, {r['created_at']})"
                        )
                        if r.get("summary"):
                            print(f"      {r['summary'][:140]}")

        elif args.query_orphans:
            limit = args.limit if args.limit and args.limit > 0 else 100
            rows = query_orphan_links(
                conn,
                user_id=user_id,
                limit=limit,
            )
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                if not rows:
                    print("No orphan links.")
                else:
                    print(f"Orphan links ({len(rows)} of max {limit}):")
                    for r in rows:
                        print(
                            f"  link_id={r['link_id']:6d} "
                            f"{r['source_id']} -[{r['link_type']}]-> "
                            f"{r['target_id']} (target missing) "
                            f"created={r['created_at']}"
                        )

        # ─── Skill registration (gz-nced6) ───────────────────────────────────
        elif args.register_skill_name:
            if args.skill_description is None:
                msg = "--register-skill requires --skill-description TEXT"
                if args.json:
                    print(json.dumps({"error": msg}))
                else:
                    print(f"Error: {msg}", file=sys.stderr)
                sys.exit(2)
            try:
                result = register_skill(
                    conn,
                    name=args.register_skill_name,
                    description=args.skill_description,
                    user_id=user_id,
                    from_patterns=args.from_patterns or [],
                    prov_agent=args.prov_agent,
                    project=args.project,
                    session_id=args.session_id,
                )
            except ValueError as e:
                if args.json:
                    print(json.dumps({"error": str(e)}))
                else:
                    print(f"Error: {e}", file=sys.stderr)
                sys.exit(2)
            except RuntimeError as e:
                if args.json:
                    print(json.dumps({"error": str(e)}))
                else:
                    print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(f"✓ Skill registered: {result['name']}")
                print(f"   skill_id: {result['skill_id']}")
                print(f"   promoted_weight: +{result['promoted_weight']:.1f}")
                if result["linked_from_patterns"]:
                    print(
                        f"   derives_from: "
                        + ", ".join(result["linked_from_patterns"])
                    )
                else:
                    print("   derives_from: (none)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
