#!/usr/bin/env python3
"""gz-nced6 — --register-skill composite primitive tests.

Verifies the composite skill-registration primitive that wires together:
  1. capture (with thought_type='skill_ref', metadata.pearl_kind='skill_ref',
     prov_activity='skill_register')
  2. Hebbian +2.0 promote
  3. derives_from links to each --from-pattern source

Each test uses an isolated TEST USER ID and cleans up its own rows so the
real user's brain is unaffected. Tests are skipped (not failed) when no
DATABASE_URL is configured.

Run: python3 -m pytest scripts/tests/test_register_skill.py -v
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional

import pytest

# Add scripts dir to path so we can import open_brain
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import open_brain  # noqa: E402


SCRIPT = Path(__file__).resolve().parents[1] / "open_brain.py"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    """Module-scoped live Postgres connection.

    Skips the whole module if no DATABASE_URL is set or psycopg2 is missing.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            db_url = open_brain._get_database_url()
        except Exception:
            pytest.skip("No DATABASE_URL configured")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 not installed")
    c = open_brain._connect()
    # Make sure schema is in place — idempotent.
    open_brain.init_schema(c)
    # init_schema's DDL runs `SET search_path TO brain;` which excludes the
    # public schema where the pgvector `vector` type lives. Capture writes
    # use `::vector` casts that need to resolve against public. Restore the
    # default search_path so the rest of the test session can capture.
    cur = c.cursor()
    try:
        cur.execute("SET search_path TO brain, public")
        c.commit()
    finally:
        cur.close()
    yield c
    c.close()


def _cleanup_user(conn, uid: str) -> None:
    """Delete every row a test user wrote across the brain tables."""
    cur = conn.cursor()
    try:
        for tbl in ("brain.kg_edges", "brain.kg_nodes",
                    "brain.knowledge_graph_edges", "brain.knowledge_graph_nodes"):
            try:
                cur.execute(f"DELETE FROM {tbl} WHERE user_id = %s", (uid,))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            cur.execute("DELETE FROM brain.atom_links WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("DELETE FROM brain.replay_log WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("DELETE FROM brain.promotions WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "DELETE FROM brain.thought_versions "
                "WHERE thought_id IN ("
                "  SELECT thought_id FROM brain.thoughts WHERE user_id = %s)",
                (uid,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("DELETE FROM brain.thoughts WHERE user_id = %s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        cur.close()


@pytest.fixture()
def test_user(conn):
    """Unique test user id scoped to this test invocation.

    Yields the user id, then deletes every row this user wrote at teardown.
    """
    uid = f"test-user-skill-{uuid.uuid4().hex[:12]}"
    yield uid
    _cleanup_user(conn, uid)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _insert_pattern(conn, user_id: str, text: str, thought_type: str = "pattern") -> str:
    """Insert a 'pattern' atom directly (bypassing LLM metadata extraction).

    Used to seed --from-pattern targets cheaply.
    """
    thought_id = open_brain._generate_thought_id()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO brain.thoughts (
                thought_id, user_id, raw_text, summary, thought_type,
                topics, people, action_items, source, session_id, project,
                prov_agent, prov_activity, was_generated_by, was_derived_from,
                source_uri, embedding, metadata, created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                'test', '', '',
                %s, 'capture', %s, NULL, NULL,
                NULL, '{}'::jsonb,
                NOW(), NOW()
            )
            """,
            (
                thought_id,
                user_id,
                text[:16384],
                text[:1000],
                thought_type,
                f"cli-user-{user_id}"[:100],
                f"activity-{thought_id}",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return thought_id


def _fetch_thought(conn, thought_id: str) -> Optional[dict]:
    """Return a single thought row as a dict (or None if missing)."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT thought_id, user_id, raw_text, thought_type, prov_activity,
                   metadata
            FROM brain.thoughts
            WHERE thought_id = %s
            """,
            (thought_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return None
    return {
        "thought_id": row[0],
        "user_id": row[1],
        "raw_text": row[2],
        "thought_type": row[3],
        "prov_activity": row[4],
        "metadata": row[5],
    }


def _fetch_promotion_rows(conn, thought_id: str) -> List[dict]:
    """Return all brain.promotions rows for a thought."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT promotion_id, thought_id, user_id, weight, reason
            FROM brain.promotions
            WHERE thought_id = %s
            ORDER BY promotion_id ASC
            """,
            (thought_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()
    return [
        {
            "promotion_id": r[0],
            "thought_id": r[1],
            "user_id": r[2],
            "weight": float(r[3]),
            "reason": r[4],
        }
        for r in rows
    ]


def _fetch_link_rows(conn, source_id: str) -> List[dict]:
    """Return atom_links rows for a given source_id."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT link_id, source_id, target_id, link_type, user_id
            FROM brain.atom_links
            WHERE source_id = %s
            ORDER BY link_id ASC
            """,
            (source_id,),
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
            "user_id": r[4],
        }
        for r in rows
    ]


def _run_cli(args: List[str], extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Run open_brain.py CLI with $USER pinned to a test user.

    Returns the CompletedProcess (stdout/stderr captured).
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        env=env,
        capture_output=True,
        text=True,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestRegisterSkillHappyPath:
    """Composite primitive happy-path tests via the in-process Python API.

    We invoke ``register_skill`` directly (rather than shelling out to the
    CLI) so the tests don't pay the per-call LLM extraction round-trip cost
    on the seed atoms — but the skill atom itself goes through the real
    capture path including embedding + metadata extraction. The CLI shape
    is tested separately in ``TestRegisterSkillCLI``.
    """

    def test_register_skill_captures_atom_with_skill_ref_kind(self, conn, test_user):
        result = open_brain.register_skill(
            conn,
            name="detect-macos-sync-conflicts",
            description=(
                "find . -name '* [0-9]*' surfaces macOS Files-app sync-conflict "
                "duplicates; sweep + install pre-commit guard."
            ),
            user_id=test_user,
            from_patterns=[],
        )

        atom = _fetch_thought(conn, result["skill_id"])
        assert atom is not None
        assert atom["user_id"] == test_user
        assert atom["thought_type"] == "skill_ref"
        # metadata is a dict from psycopg2 JSONB; defensive parse if str.
        meta = atom["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("pearl_kind") == "skill_ref"
        assert meta.get("skill_name") == "detect-macos-sync-conflicts"
        assert atom["raw_text"].startswith("SKILL: detect-macos-sync-conflicts\n")

    def test_register_skill_promotes_immediately_with_weight_2(self, conn, test_user):
        result = open_brain.register_skill(
            conn,
            name="hebbian-promote-validation",
            description=(
                "Promote pattern atoms after validation so future recall ranks "
                "them above un-promoted siblings."
            ),
            user_id=test_user,
            from_patterns=[],
        )

        promos = _fetch_promotion_rows(conn, result["skill_id"])
        assert len(promos) == 1
        assert promos[0]["weight"] == 2.0
        assert promos[0]["thought_id"] == result["skill_id"]
        assert promos[0]["user_id"] == test_user
        # And the API contract reports the same weight.
        assert result["promoted_weight"] == 2.0

    def test_register_skill_writes_derives_from_links(self, conn, test_user):
        p1 = _insert_pattern(conn, test_user, "pattern 1: serialize git-index access")
        p2 = _insert_pattern(conn, test_user, "pattern 2: --only flag scoped commits")

        result = open_brain.register_skill(
            conn,
            name="parallel-implementer-race-mitigation",
            description=(
                "Serialize git-index access via --only when 2+ implementer "
                "subagents commit in parallel; recovery via reset --soft HEAD~1."
            ),
            user_id=test_user,
            from_patterns=[p1, p2],
        )

        links = _fetch_link_rows(conn, result["skill_id"])
        assert len(links) == 2
        targets = {(lr["target_id"], lr["link_type"]) for lr in links}
        assert (p1, "derives_from") in targets
        assert (p2, "derives_from") in targets
        # The API contract reports both targets in insertion order.
        assert result["linked_from_patterns"] == [p1, p2]

    def test_register_skill_zero_from_patterns_writes_no_links(self, conn, test_user):
        result = open_brain.register_skill(
            conn,
            name="no-source-pattern-skill",
            description=(
                "A skill with no source patterns is fine — the agent registered "
                "it before any specific pattern surfaced."
            ),
            user_id=test_user,
            from_patterns=None,
        )

        links = _fetch_link_rows(conn, result["skill_id"])
        assert links == []
        assert result["linked_from_patterns"] == []


class TestRegisterSkillValidation:
    """Negative-path tests: bad inputs must reject BEFORE any DB write."""

    def test_register_skill_invalid_name_rejected(self, conn, test_user):
        with pytest.raises(ValueError) as exc:
            open_brain.register_skill(
                conn,
                name="BadName!",  # caps + punctuation
                description="A valid description that is long enough to pass length checks.",
                user_id=test_user,
            )
        assert "invalid skill name" in str(exc.value).lower()

        # And no atom, promotion, or link was written under this user.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.thoughts WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM brain.promotions WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()

    def test_register_skill_missing_description_rejected(self, conn, test_user):
        """argparse-level required-flag check: --register-skill without
        --skill-description must exit non-zero.

        We invoke the CLI here because argparse-level rejection is what we
        promise users — the Python API expresses the same constraint via
        ``_validate_skill_description`` raising on an empty string.
        """
        proc = _run_cli(
            ["--register-skill", "valid-skill-name"],
            extra_env={"USER": test_user},
        )
        # Exit code 2 (argparse-level error path in our CLI).
        assert proc.returncode != 0
        stderr_lower = proc.stderr.lower()
        # CLI emits the explicit-requirement message AND stderr also carries
        # the argparse rejection — either is acceptable evidence.
        assert (
            "--register-skill requires --skill-description" in proc.stderr
            or "skill-description" in stderr_lower
        )

    def test_register_skill_too_short_description_rejected(self, conn, test_user):
        with pytest.raises(ValueError) as exc:
            open_brain.register_skill(
                conn,
                name="short-desc-skill",
                description="abc",  # 3 chars, below the 10-char floor
                user_id=test_user,
            )
        assert "too short" in str(exc.value).lower()

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.thoughts WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()

    def test_register_skill_too_long_description_rejected(self, conn, test_user):
        long_desc = "x" * 5000
        with pytest.raises(ValueError) as exc:
            open_brain.register_skill(
                conn,
                name="long-desc-skill",
                description=long_desc,
                user_id=test_user,
            )
        assert "too long" in str(exc.value).lower()

        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.thoughts WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()

    def test_register_skill_missing_from_pattern_rejected(self, conn, test_user):
        bogus = "brain-fake-deadbeef"
        with pytest.raises(RuntimeError) as exc:
            open_brain.register_skill(
                conn,
                name="missing-pattern-skill",
                description=(
                    "A skill claiming to derive from a pattern that does not "
                    "exist must be rejected, not silently dropped."
                ),
                user_id=test_user,
                from_patterns=[bogus],
            )
        msg = str(exc.value)
        assert bogus in msg
        assert "not in user scope" in msg

        # Critically: NO atom, NO promotion, NO link was written.
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM brain.thoughts WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM brain.promotions WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM brain.atom_links WHERE user_id = %s",
                (test_user,),
            )
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()


class TestRegisterSkillSemantics:
    """Soft-warn, prov_activity, and JSON output shape."""

    def test_register_skill_duplicate_name_warns_but_succeeds(self, conn, test_user, capsys):
        first = open_brain.register_skill(
            conn,
            name="duplicate-skill-test",
            description=(
                "First registration of this skill — sets the canonical name "
                "that the second registration will collide with."
            ),
            user_id=test_user,
        )
        # Drain any captured output from the first call.
        capsys.readouterr()

        second = open_brain.register_skill(
            conn,
            name="duplicate-skill-test",
            description=(
                "Second registration of this skill — should succeed AND emit a "
                "stderr warning referencing the first skill_id."
            ),
            user_id=test_user,
        )

        captured = capsys.readouterr()
        warn = captured.err
        assert "already exists" in warn.lower()
        assert first["skill_id"] in warn
        # Both registrations produced distinct atoms.
        assert first["skill_id"] != second["skill_id"]

        # Both atoms are durable and both promotions exist.
        a1 = _fetch_thought(conn, first["skill_id"])
        a2 = _fetch_thought(conn, second["skill_id"])
        assert a1 is not None and a1["thought_type"] == "skill_ref"
        assert a2 is not None and a2["thought_type"] == "skill_ref"

    def test_register_skill_prov_activity_is_skill_register(self, conn, test_user):
        result = open_brain.register_skill(
            conn,
            name="prov-activity-skill",
            description=(
                "Verify the prov_activity on the captured atom carries the "
                "skill-register activity, not the default 'capture'."
            ),
            user_id=test_user,
        )
        atom = _fetch_thought(conn, result["skill_id"])
        assert atom is not None
        assert atom["prov_activity"] == "skill_register"

    def test_register_skill_json_output_shape(self, conn, test_user):
        """--json flag returns {skill_id, name, promoted_weight,
        linked_from_patterns}.

        Exercised via the CLI to confirm the documented stdout shape.
        """
        # Seed a source pattern so linked_from_patterns is non-empty.
        p1 = _insert_pattern(conn, test_user, "seed pattern for JSON shape test")

        proc = _run_cli(
            [
                "--register-skill", "json-shape-skill",
                "--skill-description",
                "Verify the --json flag emits the documented object shape on stdout.",
                "--from-pattern", p1,
                "--json",
            ],
            extra_env={"USER": test_user},
        )
        assert proc.returncode == 0, (
            f"CLI exit {proc.returncode}: stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )

        # Parse the LAST JSON object on stdout. capture()'s internal
        # downstream paths may print informational lines, so be defensive.
        stdout = proc.stdout.strip()
        # Find the JSON object the dispatcher prints — it's the only line
        # that starts with '{' and parses as JSON. Try the whole stdout
        # first, then fall back to per-line parsing.
        parsed = None
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    parsed = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        assert parsed is not None, f"no JSON object on stdout: {stdout!r}"

        # Required keys, exact shape.
        assert set(parsed.keys()) == {
            "skill_id", "name", "promoted_weight", "linked_from_patterns"
        }
        assert parsed["name"] == "json-shape-skill"
        assert parsed["promoted_weight"] == 2.0
        assert parsed["linked_from_patterns"] == [p1]
        assert isinstance(parsed["skill_id"], str)
        assert parsed["skill_id"].startswith("brain-")


# ─── Module teardown ─────────────────────────────────────────────────────────


def teardown_module(module):
    """Belt-and-suspenders: clean up any test-user rows left behind by a
    test that exited before its fixture teardown ran (e.g. an interrupted
    pytest run).

    The per-test fixture handles the normal path; this catches the rare
    "abandoned test user" case so the brain doesn't accumulate test residue
    over time.
    """
    try:
        conn = open_brain._connect()
    except Exception:
        return
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM brain.thoughts WHERE user_id LIKE 'test-user-skill-%%'"
            )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass
