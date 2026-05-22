"""brain-W1-S7: VF_eps probe library.

Verified-forgetting primitive (Lin/Li/Chen 2026 §12.1). Generates n=300 probes
from a pre-delete ProbeSeedSnapshot, scores residue detection, computes both
Hoeffding (loose, distribution-free) and exact-binomial (tight) confidence
bounds — both are recorded distinctly so a reader can see which number is
being quoted.

Reference: optivai-builder/src/agents/vf-probe.ts (the TS design source we
ported from; algorithmic provenance only).

Bead: gz-dcwjp (brain-W1-S7).
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# gz-j5mj7 — fenced-block stripper. Some Claude responses wrap JSON in
# ```json ... ``` fences; the prior strip("`") + lstrip("json") was
# fragile (it stripped any of those chars, not the literal keyword).
# This regex matches an opening fence (with optional ``json`` hint) and
# the closing fence on the trailing line. MULTILINE so both anchors fire.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$", re.MULTILINE)


def _stable_seed(text: str) -> int:
    """Stable cross-process seed from text.

    Python's built-in ``hash()`` is per-process randomized (PEP 456) — two
    processes computing ``hash(thought_id)`` get different values, breaking
    probe-set reproducibility. ``hashlib.md5`` is deterministic across
    runs; truncate the hex digest to 32 bits for the
    ``random.Random(seed)`` constructor.

    Cryptographic strength is irrelevant here — md5 is used purely as a
    deterministic mixer. The output is fed only to the PRNG seed, never
    to authentication / integrity paths.
    """
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)

# ─── Defaults (calibrated VF_eps parameters) ─────────────────────────────────

DEFAULT_N: int = 300
DEFAULT_EPSILON: float = 0.05
TOPN_NEIGHBORS: int = 50  # neighbors captured into the snapshot pre-delete

# Probe distribution at default n=300 — the 40/30/20/10 ratio per Lin §12.1.
# Scaled proportionally for other n in verify_forgetting().
DEFAULT_DISTRIBUTION: Dict[str, int] = {
    "semantic": 120,   # 40% — top-NN sexprs of the forgotten text
    "paraphrase": 90,  # 30% — Claude Haiku paraphrases (falls back to partial)
    "partial": 60,     # 20% — sliding-window substring fragments
    "perturb": 30,     # 10% — Gaussian-noise perturbation of the embedding
}


# ─── Public dataclasses ─────────────────────────────────────────────────────


@dataclass
class ProbeSeedSnapshot:
    """Pre-delete snapshot the probe library runs probes against.

    Captured BEFORE the forget operation deletes the live row. Contains
    everything the probe generators need to construct realistic probes
    that would surface the forgotten content if any residue remained.

    Attributes
    ----------
    forgotten_thought_id
        The ``thought_id`` of the row being forgotten.
    forgotten_text
        Full ``raw_text`` of the forgotten thought.
    forgotten_summary
        The thought's ``summary`` (may be ``None``).
    forgotten_topics
        List of topics from the thought's metadata.
    forgotten_embedding
        The 768-dim embedding vector at pre-delete time.
        ``None`` only if the row had no embedding.
    neighbors_sexprs
        Top-N (``<= TOPN_NEIGHBORS``) ``raw_text`` snippets of the semantic
        neighbors captured pre-delete (``sampledFromSnapshot=True``).
        Empty if no embedding query was possible.
    user_id
        Scoping for probe queries — probes only consider this user's results.
    """

    forgotten_thought_id: str
    forgotten_text: str
    forgotten_summary: Optional[str]
    forgotten_topics: List[str] = field(default_factory=list)
    forgotten_embedding: Optional[List[float]] = None
    neighbors_sexprs: List[str] = field(default_factory=list)
    user_id: str = ""


@dataclass
class ProbeResult:
    """Outcome of one probe query against the post-delete live store."""

    probe_id: int
    probe_kind: str               # "semantic" | "paraphrase" | "partial" | "perturb"
    probe_text: Optional[str]     # the text the probe submitted (None for vector-only)
    probe_vector_used: bool       # True for "perturb" probes
    surfaced_forgotten: bool      # did the probe surface the forgotten thought?
    top_result_thought_id: Optional[str]
    top_result_similarity: Optional[float]


@dataclass
class VerifyForgettingResult:
    """The result of a full :func:`verify_forgetting` run."""

    forgotten_thought_id: str
    n: int                                  # total probes run
    k: int                                  # probes that surfaced the forgotten thought
    epsilon: float                          # epsilon-bound target
    accepted: bool                          # k == 0 → True (zero residue)
    hoeffdingBound: float                   # exp(-2*n*eps**2) — loose
    hoeffdingConfidence: float              # 1 - hoeffdingBound
    exactBinomialBound: float               # (1-eps)**n — tight
    exactBinomialConfidence: float          # 1 - exactBinomialBound
    probeQuality: Dict[str, Any]            # {n, distribution, sampledFromSnapshot}
    probes: List[Dict[str, Any]]            # per-probe results (asdict of ProbeResult)


# ─── Statistical bound functions ────────────────────────────────────────────


def hoeffding_bound(n: int, epsilon: float) -> float:
    """Hoeffding one-sided bound: ``exp(-2 * n * eps**2)``.

    Conservative / loose. At ``n=300, eps=0.05`` → ``≈ 0.2231``
    (77.69% confidence).

    Raises
    ------
    ValueError
        If ``n`` is negative or ``epsilon`` is outside ``(0, 1)``.
    """
    if not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
        raise ValueError("hoeffding_bound: n must be a non-negative finite number")
    if (
        not isinstance(epsilon, (int, float))
        or not math.isfinite(epsilon)
        or epsilon <= 0
        or epsilon >= 1
    ):
        raise ValueError("hoeffding_bound: epsilon must be in (0, 1)")
    if n == 0:
        # No probes → bound is uninformative (1.0)
        return 1.0
    return math.exp(-2.0 * n * epsilon * epsilon)


def exact_binomial_bound(n: int, epsilon: float) -> float:
    """Exact binomial bound for ``k=0``: ``(1 - eps) ** n``.

    Tight. At ``n=300, eps=0.05`` → ``≈ 2.075e-7`` (99.9999793% confidence).
    This is the headline confidence — the figure to quote when describing
    the strength of the forget guarantee.

    Raises
    ------
    ValueError
        If ``n`` is negative or ``epsilon`` is outside ``(0, 1)``.
    """
    if not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
        raise ValueError(
            "exact_binomial_bound: n must be a non-negative finite number"
        )
    if (
        not isinstance(epsilon, (int, float))
        or not math.isfinite(epsilon)
        or epsilon <= 0
        or epsilon >= 1
    ):
        raise ValueError("exact_binomial_bound: epsilon must be in (0, 1)")
    if n == 0:
        return 1.0
    return (1.0 - epsilon) ** n


def confidence(bound: float) -> float:
    """Convert a residue-probability bound to confidence (``1 - bound``)."""
    if not isinstance(bound, (int, float)) or not math.isfinite(bound):
        raise ValueError("confidence: bound must be a finite number")
    return 1.0 - bound


# ─── Embedding coercion helper ──────────────────────────────────────────────


def _coerce_embedding(raw: Any) -> Optional[List[float]]:
    """Coerce a pgvector return value to a list of floats.

    psycopg2 returns pgvector columns as a string ``"[0.1, 0.2, ...]"`` by
    default; some adapters return a real list. Both are handled here. Returns
    ``None`` for unparseable / missing values rather than raising — the
    snapshot is permitted to lack an embedding, the perturb-probe generator
    handles the empty case.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
    try:
        s = str(raw).strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        if not s:
            return None
        return [float(x) for x in s.split(",")]
    except (TypeError, ValueError, AttributeError):
        return None


# ─── Snapshot construction (pre-delete capture) ─────────────────────────────


def build_probe_seed_snapshot(
    conn,
    thought_id: str,
    user_id: str,
    topn: int = TOPN_NEIGHBORS,
) -> ProbeSeedSnapshot:
    """Capture pre-delete state for the probe library.

    MUST be called BEFORE the live row is deleted (delete-after-verify
    pattern). Pulls the thought + its embedding + top-N semantic neighbors
    via pgvector cosine distance.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    thought_id
        Target ``thought_id`` to snapshot.
    user_id
        Caller scope — the row MUST belong to this user.
    topn
        Number of semantic neighbors to capture.

    Raises
    ------
    RuntimeError
        If ``thought_id`` is not in ``user_id``'s scope.
    """
    if not isinstance(thought_id, str) or not thought_id:
        raise ValueError(
            "build_probe_seed_snapshot: thought_id must be a non-empty string"
        )
    if not isinstance(user_id, str) or not user_id:
        raise ValueError(
            "build_probe_seed_snapshot: user_id must be a non-empty string"
        )
    if not isinstance(topn, int) or topn < 0:
        raise ValueError(
            "build_probe_seed_snapshot: topn must be a non-negative integer"
        )

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT raw_text, summary, topics, embedding
            FROM brain.thoughts
            WHERE thought_id = %s AND user_id = %s
            """,
            (thought_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "build_probe_seed_snapshot: thought "
                f"{thought_id} not in user scope (user={user_id})"
            )
        raw_text, summary, topics_jsonb, embedding_raw = row

        embedding_list = _coerce_embedding(embedding_raw)

        # Top-N semantic neighbors (excluding the forgotten thought itself).
        # On any pgvector / driver error, degrade to an empty list — the
        # generator falls back to forgotten_text-derived probes.
        neighbors_sexprs: List[str] = []
        if embedding_list is not None and topn > 0:
            try:
                vec_literal = "[" + ",".join(repr(float(v)) for v in embedding_list) + "]"
                cur.execute(
                    """
                    SELECT raw_text
                    FROM brain.thoughts
                    WHERE user_id = %s AND thought_id != %s
                          AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (user_id, thought_id, vec_literal, topn),
                )
                neighbors_sexprs = [r[0] for r in cur.fetchall() if r[0]]
            except Exception:
                conn.rollback()
                neighbors_sexprs = []

        topics_list = topics_jsonb if isinstance(topics_jsonb, list) else []

        return ProbeSeedSnapshot(
            forgotten_thought_id=thought_id,
            forgotten_text=raw_text or "",
            forgotten_summary=summary,
            forgotten_topics=topics_list,
            forgotten_embedding=embedding_list,
            neighbors_sexprs=neighbors_sexprs,
            user_id=user_id,
        )
    finally:
        cur.close()


# ─── Probe generators (per kind) ────────────────────────────────────────────


def _generate_semantic_probes(
    snapshot: ProbeSeedSnapshot, count: int
) -> List[str]:
    """Generate ``count`` semantic-neighbor probes.

    Each probe is the ``raw_text`` of a known semantic neighbor of the
    forgotten content. If a residue remains, a semantic search for this
    neighbor should NOT surface the (deleted) forgotten thought.

    Rotates through neighbors if ``count > len(neighbors)``; falls back to
    the forgotten text if no neighbors are available.
    """
    if count <= 0:
        return []
    src = snapshot.neighbors_sexprs if snapshot.neighbors_sexprs else [
        snapshot.forgotten_text or snapshot.forgotten_thought_id
    ]
    return [src[i % len(src)] for i in range(count)]


def _generate_partial_probes(
    snapshot: ProbeSeedSnapshot, count: int
) -> List[str]:
    """Generate ``count`` partial-fragment probes: substrings of ``forgotten_text``.

    Sliding window with variable starts/lengths. Each fragment is a substring
    of ``forgotten_text`` that would match if any residue remained. Uses a
    PRNG seeded from ``forgotten_thought_id`` so probes are reproducible.
    """
    if count <= 0:
        return []
    text = snapshot.forgotten_text or ""
    if not text:
        return [""] * count
    length_text = len(text)
    if length_text < 8:
        return [text] * count

    # gz-j5mj7 — use _stable_seed instead of hash(). hash() is per-process
    # randomized, so probe sets differed across runs for the same input;
    # _stable_seed (md5-based) is reproducible.
    rng = random.Random(_stable_seed(snapshot.forgotten_thought_id))
    probes: List[str] = []
    for _ in range(count):
        max_start = max(0, length_text - 8)
        start = rng.randint(0, max_start)
        max_len = min(32, length_text - start)
        # min_len always <= max_len since length_text >= 8 and start <= length_text-8
        length = rng.randint(8, max_len)
        probes.append(text[start:start + length])
    return probes


def _generate_paraphrase_probes(
    snapshot: ProbeSeedSnapshot, count: int
) -> List[str]:
    """Generate ``count`` paraphrase probes via Claude Haiku (if available).

    Falls back to partial-fragment probes if ``ANTHROPIC_API_KEY`` is not
    set OR the API call fails. Maintains the probe budget (still returns
    exactly ``count`` items).
    """
    if count <= 0:
        return []
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _generate_partial_probes(snapshot, count)

    try:
        import json

        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"Rephrase the following text in {count} different ways. "
            f"Return ONLY a JSON array of {count} strings, no explanation, "
            f"no markdown fencing.\n\n"
            f"Text: {(snapshot.forgotten_text or '')[:500]}"
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=count * 60,
            messages=[{"role": "user", "content": prompt}],
        )
        text_out = msg.content[0].text if msg.content else "[]"
        text_out = text_out.strip()
        # gz-j5mj7 — strip Markdown fences if the model emitted them.
        # Use the module-level _FENCE_RE instead of the prior fragile
        # strip("`") + lstrip("json") (which stripped any of those chars,
        # not the literal "json" keyword).
        text_out = _FENCE_RE.sub("", text_out).strip()
        paraphrases = json.loads(text_out)
        if not isinstance(paraphrases, list):
            raise ValueError("paraphrase response is not a JSON array")

        paraphrases = [str(p) for p in paraphrases]
        if len(paraphrases) < count:
            # Pad with partial fragments to preserve the probe budget
            paraphrases.extend(
                _generate_partial_probes(snapshot, count - len(paraphrases))
            )
        return paraphrases[:count]
    except Exception:
        return _generate_partial_probes(snapshot, count)


def _generate_perturb_probes(
    snapshot: ProbeSeedSnapshot, count: int
) -> List[List[float]]:
    """Generate ``count`` embedding-perturbation probes (vector probes).

    Small Gaussian noise on the forgotten embedding; the score function
    queries pgvector cosine-similarity with these noisy vectors. If the
    snapshot has no embedding, returns ``count`` empty vectors — the
    score function treats these as no-hit probes.
    """
    if count <= 0:
        return []
    if not snapshot.forgotten_embedding:
        return [[] for _ in range(count)]
    base = snapshot.forgotten_embedding
    # gz-j5mj7 — same _stable_seed swap as in _generate_partial_probes.
    rng = random.Random(_stable_seed(snapshot.forgotten_thought_id))
    sigma = 0.01  # small noise — preserves direction at fine granularity
    return [
        [v + rng.gauss(0.0, sigma) for v in base]
        for _ in range(count)
    ]


# ─── Per-probe scoring ──────────────────────────────────────────────────────


def _score_probe(
    conn,
    snapshot: ProbeSeedSnapshot,
    probe_kind: str,
    probe_text: Optional[str],
    probe_vector: Optional[List[float]],
) -> ProbeResult:
    """Run one probe against the live store; detect residue.

    A probe "surfaces" the forgotten content iff the top-3 result of a
    user-scoped query includes any row whose ``thought_id`` equals
    ``forgotten_thought_id``. Since the row is (by S8 contract) already
    deleted, surfacing it indicates DB residue.

    The connection is rolled back on any query error so the test fixture's
    transaction state stays clean.

    gz-j5mj7 — note on the substring-probe path: the text-probe scoring
    uses ``ILIKE %{probe_text[:50]}%`` which is fail-closed by design.
    A probe whose 50-char prefix accidentally substring-matches an
    unrelated thought will register ``k > 0`` and trigger forget-failed-
    residue. False positives PREVENT data loss (the forget operation
    refuses to commit and the row is restored), so this bias is
    intentional — never relax it without re-grounding the VF guarantee.
    """
    cur = conn.cursor()
    top_id: Optional[str] = None
    top_sim: Optional[float] = None
    surfaced = False
    try:
        try:
            if probe_vector is not None and len(probe_vector) > 0:
                vec_literal = (
                    "[" + ",".join(repr(float(v)) for v in probe_vector) + "]"
                )
                cur.execute(
                    """
                    SELECT thought_id, 1.0 - (embedding <=> %s::vector) AS sim
                    FROM brain.thoughts
                    WHERE user_id = %s AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT 3
                    """,
                    (vec_literal, snapshot.user_id, vec_literal),
                )
            elif probe_text:
                # Hybrid: substring ILIKE under user scope. Brain's primary
                # recall is embedding-based, but for residue detection we
                # also need the substring path (covers the partial-fragment
                # probe semantics).
                cur.execute(
                    """
                    SELECT thought_id, 1.0 AS sim
                    FROM brain.thoughts
                    WHERE user_id = %s AND raw_text ILIKE %s
                    LIMIT 3
                    """,
                    (snapshot.user_id, f"%{probe_text[:50]}%"),
                )
            else:
                # Empty probe — no-op, returns no-hit
                return ProbeResult(
                    probe_id=-1,
                    probe_kind=probe_kind,
                    probe_text=probe_text,
                    probe_vector_used=(probe_vector is not None),
                    surfaced_forgotten=False,
                    top_result_thought_id=None,
                    top_result_similarity=None,
                )
            rows = cur.fetchall()
        except Exception:
            conn.rollback()
            rows = []

        if rows:
            top_id = rows[0][0]
            top_sim = float(rows[0][1]) if rows[0][1] is not None else None
            surfaced = any(r[0] == snapshot.forgotten_thought_id for r in rows)

        return ProbeResult(
            probe_id=-1,
            probe_kind=probe_kind,
            probe_text=probe_text,
            probe_vector_used=(probe_vector is not None),
            surfaced_forgotten=surfaced,
            top_result_thought_id=top_id,
            top_result_similarity=top_sim,
        )
    finally:
        cur.close()


# ─── Public verify_forgetting entrypoint ────────────────────────────────────


def _scaled_distribution(n: int) -> Dict[str, int]:
    """Scale the 40/30/20/10 default distribution to ``n`` and clamp sum to ``n``.

    Each bucket gets at least 1 probe (when ``n >= 4``); any rounding remainder
    is absorbed by the "semantic" bucket so the totals always equal ``n``.
    """
    if n < 4:
        # For very small n we still want all buckets representable; clamp.
        return {
            "semantic": max(1, n - 3),
            "paraphrase": 1 if n >= 2 else 0,
            "partial": 1 if n >= 3 else 0,
            "perturb": 1 if n >= 4 else 0,
        }
    scale = n / 300.0
    dist = {k: max(1, int(round(v * scale))) for k, v in DEFAULT_DISTRIBUTION.items()}
    diff = n - sum(dist.values())
    dist["semantic"] = max(1, dist["semantic"] + diff)
    return dist


def verify_forgetting(
    conn,
    snapshot: ProbeSeedSnapshot,
    n: int = DEFAULT_N,
    epsilon: float = DEFAULT_EPSILON,
    distribution: Optional[Dict[str, int]] = None,
) -> VerifyForgettingResult:
    """Run ``n`` probes against the live store; verify no residue surfaces.

    Accept iff ``k=0`` (zero probes surface the forgotten thought).

    PRECONDITION: the forgotten thought MUST already be deleted from
    ``brain.thoughts`` (delete-after-verify pattern — see S8). This function
    does NOT modify the store; it only QUERIES. If the live row still exists,
    every semantic probe will trivially find it and ``accepted=False``.

    Parameters
    ----------
    conn
        Open psycopg2 connection.
    snapshot
        Pre-delete snapshot from :func:`build_probe_seed_snapshot`.
    n
        Total number of probes (default 300).
    epsilon
        Operational target expose-rate (default 0.05).
    distribution
        Optional override of the per-kind probe counts. Defaults to
        :data:`DEFAULT_DISTRIBUTION` scaled to ``n``.

    Raises
    ------
    ValueError
        If ``n < 1`` or ``epsilon`` is outside ``(0, 1)``.
    """
    if not isinstance(snapshot, ProbeSeedSnapshot):
        raise ValueError("verify_forgetting: snapshot must be a ProbeSeedSnapshot")
    if not isinstance(n, int) or n < 1:
        raise ValueError("verify_forgetting: n must be a positive integer")
    if (
        not isinstance(epsilon, (int, float))
        or not math.isfinite(epsilon)
        or epsilon <= 0
        or epsilon >= 1
    ):
        raise ValueError("verify_forgetting: epsilon must be in (0, 1)")

    if distribution is None:
        distribution = _scaled_distribution(n)
    else:
        # Caller-supplied distribution — clamp sum to n by adjusting "semantic"
        # (or appending "semantic" if missing) so the contract always holds.
        distribution = {k: max(0, int(v)) for k, v in distribution.items()}
        diff = n - sum(distribution.values())
        distribution["semantic"] = max(0, distribution.get("semantic", 0) + diff)

    # Generate probes by kind. Text probes first, then vector probes.
    text_probes: List[Tuple[str, str]] = []
    vec_probes: List[Tuple[str, List[float]]] = []

    for text in _generate_semantic_probes(
        snapshot, distribution.get("semantic", 0)
    ):
        text_probes.append(("semantic", text))
    for text in _generate_paraphrase_probes(
        snapshot, distribution.get("paraphrase", 0)
    ):
        text_probes.append(("paraphrase", text))
    for text in _generate_partial_probes(
        snapshot, distribution.get("partial", 0)
    ):
        text_probes.append(("partial", text))
    for vec in _generate_perturb_probes(
        snapshot, distribution.get("perturb", 0)
    ):
        vec_probes.append(("perturb", vec))

    # Score each probe
    probe_results: List[ProbeResult] = []
    pid = 0
    for kind, text in text_probes:
        result = _score_probe(conn, snapshot, kind, text, None)
        result.probe_id = pid
        pid += 1
        probe_results.append(result)
    for kind, vec in vec_probes:
        result = _score_probe(conn, snapshot, kind, None, vec)
        result.probe_id = pid
        pid += 1
        probe_results.append(result)

    k = sum(1 for r in probe_results if r.surfaced_forgotten)
    accepted = (k == 0)

    hb = hoeffding_bound(n, epsilon)
    ebb = exact_binomial_bound(n, epsilon)

    return VerifyForgettingResult(
        forgotten_thought_id=snapshot.forgotten_thought_id,
        n=n,
        k=k,
        epsilon=epsilon,
        accepted=accepted,
        hoeffdingBound=hb,
        hoeffdingConfidence=confidence(hb),
        exactBinomialBound=ebb,
        exactBinomialConfidence=confidence(ebb),
        probeQuality={
            "n": n,
            "distribution": distribution,
            "sampledFromSnapshot": True,
        },
        probes=[asdict(r) for r in probe_results],
    )
