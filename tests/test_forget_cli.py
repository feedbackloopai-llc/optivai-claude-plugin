"""brain-W1-S8: --forget CLI tests.

Verifies the delete-after-verify protocol: snapshot -> delete -> probe ->
accept-or-restore. This is THE procurement-grade primitive's end-to-end flow.

The R1 fix-wave non-negotiable: structurally impossible to lose data even if
verification fails — the row is restored from the pre-delete snapshot.

The R2 fix-wave non-negotiable: BOTH bounds (Hoeffding loose + exact binomial
tight) are recorded as DISTINCT labeled columns in brain.forget_audit. The
99.9999793% headline is the exact binomial confidence at n=300/k=0/eps=0.05.

Run: python3 -m pytest tests/test_forget_cli.py -v
"""
import json
import os
import subprocess
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import open_brain  # noqa: E402
import vf_probe  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    c = psycopg2.connect(db_url)
    yield c
    c.close()


@pytest.fixture(scope="module", autouse=True)
def _run_vf_audit_migration(conn):
    """Apply the VF audit migration once before any test (idempotent)."""
    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "sql", "migrations",
        "2026-05-21-vf-audit.sql",
    )
    if not os.path.exists(migration_path):
        pytest.skip("Migration file missing: " + migration_path)
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _cleanup_thought(conn, tid):
    """Best-effort cleanup mirroring the test_vf_probe pattern.

    Each table cleanup is isolated so a missing or empty table does not
    abort the entire teardown.
    """
    cur = conn.cursor()
    try:
        for sql, params in (
            (
                "DELETE FROM brain.knowledge_graph_edges "
                "WHERE source_thought_id = %s OR target_thought_id = %s",
                (tid, tid),
            ),
            (
                "DELETE FROM brain.knowledge_graph_nodes WHERE source_thought_id = %s",
                (tid,),
            ),
            (
                "DELETE FROM brain.forget_audit WHERE forgotten_thought_id = %s",
                (tid,),
            ),
            (
                "DELETE FROM brain.thoughts WHERE thought_id = %s",
                (tid,),
            ),
        ):
            try:
                cur.execute(sql, params)
                conn.commit()
            except Exception:
                conn.rollback()
    finally:
        cur.close()


# ─── Audit schema ────────────────────────────────────────────────────────────


class TestForgetAuditSchema:
    """The brain.forget_audit substrate must exist with the dual-bound columns."""

    def test_forget_audit_table_exists(self, conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='brain' AND table_name='forget_audit'
        """)
        assert cur.fetchone() is not None
        cur.close()

    def test_both_bound_columns_present(self, conn):
        """R2 fix-wave: both Hoeffding and exact-binomial as DISTINCT columns."""
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='forget_audit'
        """)
        cols = {r[0] for r in cur.fetchall()}
        cur.close()
        for required in (
            "hoeffding_bound", "hoeffding_confidence",
            "exact_binomial_bound", "exact_binomial_conf",
        ):
            assert required in cols, f"Missing column: {required}"

    def test_probe_quality_column_is_jsonb(self, conn):
        """R3 fix-wave: probe_quality_json marker for verification quality."""
        cur = conn.cursor()
        cur.execute("""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='forget_audit'
                  AND column_name='probe_quality_json'
        """)
        row = cur.fetchone()
        cur.close()
        assert row is not None, "probe_quality_json column missing"
        assert row[0] == "jsonb"

    def test_status_column_is_present(self, conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='brain' AND table_name='forget_audit'
                  AND column_name='status'
        """)
        assert cur.fetchone() is not None
        cur.close()


# ─── Happy path: a thought with no neighbors → k=0 → forgotten ──────────────


class TestForgetHappyPath:
    def test_forget_with_no_neighbors_succeeds(self, conn):
        """A unique thought → k=0 probes surface → status='forgotten'."""
        r = open_brain.capture(
            conn,
            text="X1Y2Z3 unique forget marker " + "qwer" * 30,
            user_id="vf-forget-happy",
        )
        tid = r["thought_id"]
        try:
            result = open_brain.forget_thought(
                conn, tid, "vf-forget-happy", n=30,
            )
            assert result["status"] == "forgotten"
            assert result["thought_id"] == tid

            # Row must be gone from the live store.
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone() is None

            # Audit row exists.
            cur.execute(
                "SELECT status, n, k FROM brain.forget_audit "
                "WHERE forgotten_thought_id=%s",
                (tid,),
            )
            audit = cur.fetchone()
            cur.close()
            assert audit is not None
            assert audit[0] == "forgotten"
            assert audit[1] == 30
            assert audit[2] == 0
        finally:
            _cleanup_thought(conn, tid)

    def test_forget_records_both_bounds_in_audit(self, conn):
        """R2 fix-wave: both bounds at procurement parameters in audit row."""
        r = open_brain.capture(
            conn,
            text="bound recording test " + "alpha-beta-gamma " * 20,
            user_id="vf-forget-bounds",
        )
        tid = r["thought_id"]
        try:
            open_brain.forget_thought(
                conn, tid, "vf-forget-bounds", n=300, epsilon=0.05,
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT hoeffding_bound, hoeffding_confidence,
                       exact_binomial_bound, exact_binomial_conf
                FROM brain.forget_audit
                WHERE forgotten_thought_id=%s
            """, (tid,))
            row = cur.fetchone()
            cur.close()
            assert row is not None, "audit row not written"
            # Hoeffding: exp(-2*300*0.05^2) = 0.2231 (77.69% confidence)
            assert abs(row[0] - 0.2231) < 1e-3, f"hoeffding_bound={row[0]}"
            assert abs(row[1] - 0.7769) < 1e-3, f"hoeffding_confidence={row[1]}"
            # Exact binomial: 0.95^300 = 2.075e-7 (99.9999793% confidence)
            assert abs(row[2] - 2.075e-7) < 1e-8, f"exact_binomial_bound={row[2]}"
            assert abs(row[3] - 0.99999979) < 1e-6, f"exact_binomial_conf={row[3]}"
        finally:
            _cleanup_thought(conn, tid)

    def test_forget_audit_records_prov_agent(self, conn):
        r = open_brain.capture(
            conn,
            text="prov-agent recording for forget audit",
            user_id="vf-forget-prov",
        )
        tid = r["thought_id"]
        try:
            open_brain.forget_thought(
                conn, tid, "vf-forget-prov", n=30,
                prov_agent="custom-forget-agent",
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT prov_agent, prov_activity
                FROM brain.forget_audit
                WHERE forgotten_thought_id=%s
            """, (tid,))
            row = cur.fetchone()
            cur.close()
            assert row is not None
            assert row[0] == "custom-forget-agent"
            assert row[1] == "forget"
        finally:
            _cleanup_thought(conn, tid)

    def test_forget_audit_default_prov_agent_derived_from_user(self, conn):
        r = open_brain.capture(
            conn,
            text="default prov_agent derivation test",
            user_id="vf-forget-default-prov",
        )
        tid = r["thought_id"]
        try:
            open_brain.forget_thought(conn, tid, "vf-forget-default-prov", n=30)
            cur = conn.cursor()
            cur.execute(
                "SELECT prov_agent FROM brain.forget_audit "
                "WHERE forgotten_thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None
            # Default derives from "manual" + user_id
            assert row[0] == "cli-user-vf-forget-default-prov"
        finally:
            _cleanup_thought(conn, tid)


# ─── PS scoping (user isolation) ─────────────────────────────────────────────


class TestForgetPsScoping:
    def test_forget_cross_user_rejected(self, conn):
        """userB cannot forget userA's thought; row stays live."""
        r = open_brain.capture(
            conn,
            text="userA private content for PS-scope test",
            user_id="vf-forget-userA",
        )
        tid = r["thought_id"]
        try:
            with pytest.raises(RuntimeError, match="not in user scope"):
                open_brain.forget_thought(
                    conn, tid, "vf-forget-userB", n=30,
                )
            # Row must still exist after a rejected cross-user forget.
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone() is not None
            cur.close()
        finally:
            _cleanup_thought(conn, tid)


# ─── FK cascade: thought_versions are removed on forget ─────────────────────


class TestForgetVersionsCascade:
    def test_forget_cascades_to_thought_versions(self, conn):
        """ON DELETE CASCADE on brain.thought_versions wipes versions too."""
        r = open_brain.capture(
            conn,
            text="version cascade unique-text-for-forget-test",
            user_id="vf-forget-cascade",
        )
        tid = r["thought_id"]
        try:
            # Snapshot so a version row exists.
            open_brain.snapshot_thought(conn, tid, "vf-forget-cascade")
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM brain.thought_versions "
                "WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == 1

            # Forget the thought.
            result = open_brain.forget_thought(
                conn, tid, "vf-forget-cascade", n=30,
            )
            assert result["status"] == "forgotten"

            # Versions must be gone (CASCADE).
            cur.execute(
                "SELECT COUNT(*) FROM brain.thought_versions "
                "WHERE thought_id=%s",
                (tid,),
            )
            assert cur.fetchone()[0] == 0
            cur.close()
        finally:
            _cleanup_thought(conn, tid)


# ─── R1 fix-wave: restore-on-residue invariant ──────────────────────────────


class TestForgetRestoreOnResidue:
    """The non-negotiable R1 fix-wave: data is never lost.

    If verification surfaces residue (k>0), the row is re-INSERTed from the
    pre-delete snapshot. The audit row records the failure but the user's
    content is preserved.
    """

    def test_restore_path_called_when_verification_rejects(
        self, conn, monkeypatch
    ):
        """Force k>0 via a monkeypatch; assert the thought is restored."""
        # Capture a thought
        r = open_brain.capture(
            conn,
            text="restore-on-residue test " + "xxxxx " * 20,
            user_id="vf-forget-restore",
        )
        tid = r["thought_id"]

        original_raw_text = "restore-on-residue test " + "xxxxx " * 20

        # Patch verify_forgetting to ALWAYS reject (k>0). The forget protocol
        # must then restore the row.
        def fake_verify(conn_in, snapshot, n=300, epsilon=0.05,
                        distribution=None):
            return vf_probe.VerifyForgettingResult(
                forgotten_thought_id=snapshot.forgotten_thought_id,
                n=n,
                k=5,
                epsilon=epsilon,
                accepted=False,
                hoeffdingBound=vf_probe.hoeffding_bound(n, epsilon),
                hoeffdingConfidence=vf_probe.confidence(
                    vf_probe.hoeffding_bound(n, epsilon)
                ),
                exactBinomialBound=vf_probe.exact_binomial_bound(n, epsilon),
                exactBinomialConfidence=vf_probe.confidence(
                    vf_probe.exact_binomial_bound(n, epsilon)
                ),
                probeQuality={
                    "n": n,
                    "distribution": {"semantic": n},
                    "sampledFromSnapshot": True,
                },
                probes=[],
            )

        monkeypatch.setattr(vf_probe, "verify_forgetting", fake_verify)

        try:
            result = open_brain.forget_thought(
                conn, tid, "vf-forget-restore", n=30,
            )
            assert result["status"] == "forget-failed-residue"

            # Row MUST be restored — the procurement-grade R1 invariant.
            cur = conn.cursor()
            cur.execute(
                "SELECT raw_text FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None, "Row was NOT restored — R1 invariant violated"
            assert row[0].startswith("restore-on-residue test")
            assert "xxxxx" in row[0]

            # Audit row records the failed attempt with status code.
            cur = conn.cursor()
            cur.execute(
                "SELECT status, k FROM brain.forget_audit "
                "WHERE forgotten_thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None
            assert row[0] == "forget-failed-residue"
            assert row[1] == 5
        finally:
            _cleanup_thought(conn, tid)

    def test_restore_path_called_when_verification_errors(
        self, conn, monkeypatch
    ):
        """If verify_forgetting raises, restore the row + record error status."""
        r = open_brain.capture(
            conn,
            text="restore-on-error test " + "yyyy " * 20,
            user_id="vf-forget-error",
        )
        tid = r["thought_id"]

        def boom(conn_in, snapshot, n=300, epsilon=0.05, distribution=None):
            raise RuntimeError("simulated verification failure")

        monkeypatch.setattr(vf_probe, "verify_forgetting", boom)

        try:
            result = open_brain.forget_thought(
                conn, tid, "vf-forget-error", n=30,
            )
            assert result["status"] == "forget-failed-error"

            # Row must be restored despite the verification error.
            cur = conn.cursor()
            cur.execute(
                "SELECT raw_text FROM brain.thoughts WHERE thought_id=%s",
                (tid,),
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None, "Row was NOT restored on verify error"
            assert row[0].startswith("restore-on-error test")
        finally:
            _cleanup_thought(conn, tid)


# ─── CLI argparse / dispatcher ──────────────────────────────────────────────


class TestForgetCli:
    """End-to-end CLI surface tests via subprocess."""

    def _cli_cwd(self):
        return os.path.join(os.path.dirname(__file__), "..")

    def test_help_includes_forget_flags(self):
        result = subprocess.run(
            ["python3", "scripts/open_brain.py", "--help"],
            capture_output=True, text=True,
            cwd=self._cli_cwd(),
        )
        assert result.returncode == 0, f"--help failed: {result.stderr}"
        assert "--forget" in result.stdout
        assert "--epsilon" in result.stdout
        # --n is a common short flag; argparse prints "--n N" in help
        assert "--n " in result.stdout or "--n\n" in result.stdout or \
            "Number of probes" in result.stdout

    def test_forget_json_output_schema(self, conn):
        """End-to-end CLI: --forget THOUGHT_ID --json round-trip."""
        r = open_brain.capture(
            conn,
            text="json schema test unique " + "z9z9 " * 20,
            user_id="vf-forget-json",
        )
        tid = r["thought_id"]
        try:
            env = {**os.environ, "USER": "vf-forget-json"}
            proc = subprocess.run(
                [
                    "python3", "scripts/open_brain.py",
                    "--forget", tid,
                    "--n", "30",
                    "--json",
                ],
                capture_output=True, text=True,
                cwd=self._cli_cwd(),
                env=env,
            )
            assert proc.returncode == 0, (
                f"CLI failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
            data = json.loads(proc.stdout)
            assert data["thought_id"] == tid
            assert data["status"] in (
                "forgotten",
                "forget-failed-residue",
                "forget-failed-error",
            )
            if data["status"] == "forgotten":
                for field in (
                    "n", "k", "epsilon",
                    "hoeffdingBound", "hoeffdingConfidence",
                    "exactBinomialBound", "exactBinomialConfidence",
                    "probeQuality",
                ):
                    assert field in data["audit"], (
                        f"audit missing field: {field}"
                    )
        finally:
            _cleanup_thought(conn, tid)

    def test_forget_human_output_shows_both_confidences(self, conn):
        """Non-JSON output must display both Hoeffding + exact-binomial."""
        r = open_brain.capture(
            conn,
            text="human-output test unique " + "h7h7 " * 20,
            user_id="vf-forget-human",
        )
        tid = r["thought_id"]
        try:
            env = {**os.environ, "USER": "vf-forget-human"}
            proc = subprocess.run(
                [
                    "python3", "scripts/open_brain.py",
                    "--forget", tid,
                    "--n", "30",
                ],
                capture_output=True, text=True,
                cwd=self._cli_cwd(),
                env=env,
            )
            assert proc.returncode == 0, (
                f"CLI failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
            # Procurement-grade: BOTH bounds must surface
            assert "Hoeffding" in proc.stdout
            assert "Exact binomial" in proc.stdout
        finally:
            _cleanup_thought(conn, tid)
