"""test_refinery.py — VB2: batch-then-bisect Refinery tests.

Bead: fblai-r8g65 (epic mayor-v2). Implements the VB1 design note
(docs/plans/2026-06-22-mayor-vb1-refinery.md).

Proves the Refinery that upgrades VA0b's serial merge-on-pass:
  1.  batch_max=1 == VA0b serial (one merge per bead — parity guard)
  2.  all-green batch → one verify → K merged
  3.  one bad branch → bisect isolates it; K-1 good branches merge
  4.  textual conflict (solo) → conflict→re-implement (bounded)
  5.  semantic conflict (clean merge, combined V red, solo V red) → re-implement
  6.  anti-starvation scoring — old / once-retried branch merges first
  7.  refinery-attempts cap → escalate (not infinite re-implement)
  8.  single-writer — merge_batch/git_reset from a worker thread is a failure
  9.  merge-slot serialization — _MERGE_LOCK held around the batch cycle
  10. rollback atomicity — a red batch leaves HEAD byte-identical to snapshot

Uses REAL git repos in tmp_path (same harness style as
test_worktree_integration.py).

Run: python3 -m pytest scripts/tests/test_refinery.py -q
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import loop_runner as L
from loop_runner import (
    MergeCandidate,
    RefineOutcome,
    RunConfig,
    Runners,
    _MERGE_LOCK,
    _candidate_score,
    order_by_score,
    refine,
    run_mayor_loop,
)


# ---------------------------------------------------------------------------
# Real-git tmp_path harness
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=True, timeout=30,
    )


def _git_ok(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=False, timeout=30,
    )


def _skip_if_git_unavailable() -> None:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git not available")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


_WT_COUNTER = [0]


def _make_branch_candidate(
    repo: Path,
    bead_id: str,
    *,
    filename: str,
    content: str,
    priority: int = 2,
    attempts: int = 0,
    verified_at: float = 0.0,
    worktrees: Optional[List[str]] = None,
) -> MergeCandidate:
    """Create a worktree on branch mayor/<bead_id>, commit a file, return the
    candidate.  Branches off the repo's *current* HEAD."""
    _WT_COUNTER[0] += 1
    branch = f"mayor/{bead_id}"
    wt_dir = str(repo.parent / f"wt-{bead_id}-{_WT_COUNTER[0]}")
    r = _git_ok(repo, "worktree", "add", "-b", branch, wt_dir, "HEAD")
    assert r.returncode == 0, f"worktree add failed: {r.stderr}"
    wt = Path(wt_dir)
    (wt / filename).write_text(content)
    _git(wt, "add", filename)
    _git(wt, "commit", "-m", f"{bead_id}: write {filename}")
    if worktrees is not None:
        worktrees.append(wt_dir)
    return MergeCandidate(
        bead_id=bead_id,
        branch_name=branch,
        worktree_path=wt_dir,
        model="sonnet",
        verified_at=verified_at,
        priority=priority,
        attempts=attempts,
    )


def _cleanup_worktrees(repo: Path, worktrees: List[str]) -> None:
    for wt in worktrees:
        _git_ok(repo, "worktree", "remove", "--force", wt)


# ---------------------------------------------------------------------------
# Refinery runner seams scoped to a tmp repo
# ---------------------------------------------------------------------------

def _make_refinery_runners(
    repo: Path,
    *,
    verify_cmd_fn: Optional[Callable[[str], int]] = None,
    merge_spy: Optional[Callable[[List[MergeCandidate]], None]] = None,
) -> Runners:
    """Build Runners whose VB2 seams operate inside *repo* (the working branch)."""

    def _snapshot(branch: str) -> str:
        return _git(repo, "rev-parse", "HEAD").stdout.strip()

    def _reset(snapshot: str) -> None:
        _git_ok(repo, "merge", "--abort")
        if snapshot:
            _git_ok(repo, "reset", "--hard", snapshot)

    def _merge_batch(batch: List[MergeCandidate]) -> bool:
        if merge_spy is not None:
            merge_spy(batch)
        for c in batch:
            r = _git_ok(
                repo, "merge", "--no-ff", c.branch_name,
                "-m", f"Mayor: merge {c.branch_name}",
            )
            if r.returncode != 0:
                return False
        return True

    def _run_verify(cmd: str, timeout_s: int) -> int:
        if verify_cmd_fn is not None:
            return verify_cmd_fn(cmd)
        r = subprocess.run(
            cmd, shell=True, cwd=str(repo), timeout=timeout_s, check=False,
            capture_output=True,
        )
        return r.returncode

    def _teardown(worktree_path: str, branch_name: str) -> None:
        _git_ok(repo, "worktree", "remove", "--force", worktree_path)
        if branch_name:
            _git_ok(repo, "branch", "-D", branch_name)

    return Runners(
        beads_ready=MagicMock(return_value=[]),
        beads_close=MagicMock(),
        beads_update=MagicMock(),
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=MagicMock(return_value={"tokens": 0, "output": ""}),
        run_verify=_run_verify,
        merge_batch=_merge_batch,
        git_snapshot=_snapshot,
        git_reset=_reset,
        worktree_teardown=_teardown,
        beads_relabel=MagicMock(),
    )


def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="epic:refinery-test",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=25,
        budget_tokens=10_000_000,
        max_workers=1,
        batch_max=8,
        refinery_attempts_max=2,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


# ===========================================================================
# Scoring (pure) — anti-starvation
# ===========================================================================

class TestScoring:
    def test_score_is_pure_and_now_injected(self) -> None:
        c = MergeCandidate("b", "mayor/b", None, "sonnet", verified_at=10.0,
                           priority=2, attempts=0)
        s1 = _candidate_score(c, now=100.0)
        s2 = _candidate_score(c, now=100.0)
        assert s1 == s2
        # Later `now` → larger age → strictly higher score.
        assert _candidate_score(c, now=200.0) > s1

    def test_older_waits_outrank_newer(self) -> None:
        old = MergeCandidate("old", "mayor/old", None, "s", verified_at=0.0)
        new = MergeCandidate("new", "mayor/new", None, "s", verified_at=100.0)
        ordered = order_by_score([new, old], now=200.0)
        assert [c.bead_id for c in ordered] == ["old", "new"]

    def test_retried_outranks_fresh(self) -> None:
        fresh = MergeCandidate("fresh", "mayor/fresh", None, "s", verified_at=100.0,
                               attempts=0)
        retry = MergeCandidate("retry", "mayor/retry", None, "s", verified_at=100.0,
                               attempts=1)
        ordered = order_by_score([fresh, retry], now=101.0)
        assert ordered[0].bead_id == "retry"

    def test_higher_priority_bead_first(self) -> None:
        # lower priority NUMBER = higher precedence
        lo = MergeCandidate("lo", "mayor/lo", None, "s", verified_at=100.0, priority=5)
        hi = MergeCandidate("hi", "mayor/hi", None, "s", verified_at=100.0, priority=1)
        ordered = order_by_score([lo, hi], now=101.0)
        assert ordered[0].bead_id == "hi"

    def test_deterministic_tie_break_by_bead_id(self) -> None:
        a = MergeCandidate("zeta", "mayor/zeta", None, "s", verified_at=50.0, priority=2)
        b = MergeCandidate("alpha", "mayor/alpha", None, "s", verified_at=50.0, priority=2)
        ordered = order_by_score([a, b], now=50.0)
        assert [c.bead_id for c in ordered] == ["alpha", "zeta"]

    def test_order_does_not_mutate_input(self) -> None:
        cands = [
            MergeCandidate("b", "mayor/b", None, "s", verified_at=100.0),
            MergeCandidate("a", "mayor/a", None, "s", verified_at=0.0),
        ]
        before = list(cands)
        order_by_score(cands, now=200.0)
        assert cands == before


# ===========================================================================
# 1. batch_max=1 == VA0b serial (parity guard)
# ===========================================================================

class TestBatchMaxOneSerialParity:
    def test_batch_of_one_merges_one_branch_per_cycle(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        queue = [
            _make_branch_candidate(repo, f"fblai-serial-{i}",
                                   filename=f"f{i}.txt", content=f"{i}\n",
                                   worktrees=wts)
            for i in range(3)
        ]
        sizes: List[int] = []
        runners = _make_refinery_runners(
            repo, merge_spy=lambda batch: sizes.append(len(batch))
        )
        cfg = _make_cfg(batch_max=1)

        outcomes: List[RefineOutcome] = []
        # Drain one-at-a-time, exactly as the VA0b serial path would.
        while queue:
            outcomes.extend(refine(queue, runners, cfg, now=1000.0))

        assert all(o.kind == "merged" for o in outcomes)
        assert len(outcomes) == 3
        # Every merge batch was exactly size 1 (serial parity).
        assert sizes == [1, 1, 1], f"expected three size-1 batches, got {sizes}"
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 2. all-green batch → one verify → K merged
# ===========================================================================

class TestAllGreenBatch:
    def test_k_branches_one_verify_all_merged(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        queue = [
            _make_branch_candidate(repo, f"fblai-green-{i}",
                                   filename=f"g{i}.txt", content=f"{i}\n",
                                   worktrees=wts)
            for i in range(4)
        ]
        verify_calls = [0]

        def _verify(cmd: str) -> int:
            verify_calls[0] += 1
            return 0  # combined tree is green

        runners = _make_refinery_runners(repo, verify_cmd_fn=_verify)
        cfg = _make_cfg(batch_max=8)

        outcomes = refine(queue, runners, cfg, now=1000.0)

        assert len(outcomes) == 4
        assert all(o.kind == "merged" for o in outcomes)
        assert verify_calls[0] == 1, "all-green batch must run V exactly once"
        # All four files landed on the working branch.
        for i in range(4):
            assert (repo / f"g{i}.txt").exists()
        assert not queue, "queue fully drained"
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 3. one bad branch → bisect isolates it; good branches merge
# ===========================================================================

class TestBisectIsolatesOffender:
    def test_one_semantic_offender_isolated_good_branches_merge(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        # 3 good branches (distinct files) + 1 bad branch (creates bad.txt).
        good = [
            _make_branch_candidate(repo, f"fblai-good-{i}",
                                   filename=f"ok{i}.txt", content=f"{i}\n",
                                   worktrees=wts)
            for i in range(3)
        ]
        bad = _make_branch_candidate(repo, "fblai-bad",
                                     filename="bad.txt", content="poison\n",
                                     worktrees=wts)
        queue = [good[0], good[1], bad, good[2]]

        # V is red whenever bad.txt is present on the integrated tree.
        def _verify(cmd: str) -> int:
            return 1 if (repo / "bad.txt").exists() else 0

        runners = _make_refinery_runners(repo, verify_cmd_fn=_verify)
        cfg = _make_cfg(batch_max=8)

        outcomes = refine(queue, runners, cfg, now=1000.0)

        by_id = {o.candidate.bead_id: o.kind for o in outcomes}
        assert by_id["fblai-good-0"] == "merged"
        assert by_id["fblai-good-1"] == "merged"
        assert by_id["fblai-good-2"] == "merged"
        assert by_id["fblai-bad"] == "reimplement", by_id
        # Good files landed; bad.txt did not.
        for i in range(3):
            assert (repo / f"ok{i}.txt").exists()
        assert not (repo / "bad.txt").exists()
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 4. textual conflict (solo) → conflict→re-implement
# ===========================================================================

class TestTextualConflict:
    def test_textual_conflict_branch_reimplements_nothing_lands(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        # Branch edits shared.txt off HEAD...
        (repo / "shared.txt").write_text("base\n")
        _git(repo, "add", "shared.txt")
        _git(repo, "commit", "-m", "add shared")
        cand = _make_branch_candidate(repo, "fblai-textconf",
                                      filename="shared.txt", content="branch side\n",
                                      worktrees=wts)
        # ...then the working branch advances shared.txt divergently.
        (repo / "shared.txt").write_text("working side\n")
        _git(repo, "add", "shared.txt")
        _git(repo, "commit", "-m", "working advances shared")
        snap = _head(repo)

        runners = _make_refinery_runners(repo)  # verify never reached (merge conflicts)
        cfg = _make_cfg(batch_max=8)
        queue = [cand]

        outcomes = refine(queue, runners, cfg, now=1000.0)

        assert len(outcomes) == 1
        assert outcomes[0].kind == "reimplement"
        # Nothing landed; HEAD byte-identical to pre-batch snapshot.
        assert _head(repo) == snap
        assert (repo / "shared.txt").read_text() == "working side\n"
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 5. semantic conflict (clean merge, V red solo) → re-implement
# ===========================================================================

class TestSemanticConflict:
    def test_clean_merge_but_v_red_reimplements(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        cand = _make_branch_candidate(repo, "fblai-semantic",
                                      filename="feature.txt", content="x\n",
                                      worktrees=wts)
        snap = _head(repo)

        # Merge applies cleanly, but V is always red (semantic break).
        runners = _make_refinery_runners(repo, verify_cmd_fn=lambda cmd: 1)
        cfg = _make_cfg(batch_max=8)
        queue = [cand]

        outcomes = refine(queue, runners, cfg, now=1000.0)

        assert outcomes[0].kind == "reimplement"
        # Rolled back: feature.txt not on working branch, HEAD unchanged.
        assert not (repo / "feature.txt").exists()
        assert _head(repo) == snap
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 6. anti-starvation — old/retried branch merges first
# ===========================================================================

class TestAntiStarvation:
    def test_old_branch_selected_before_newer_when_batch_limited(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        # batch_max=1 so only the top-scored candidate merges this cycle.
        old = _make_branch_candidate(repo, "fblai-old",
                                     filename="old.txt", content="o\n",
                                     verified_at=0.0, worktrees=wts)
        new = _make_branch_candidate(repo, "fblai-new",
                                     filename="new.txt", content="n\n",
                                     verified_at=900.0, worktrees=wts)
        merged_order: List[str] = []
        runners = _make_refinery_runners(
            repo, merge_spy=lambda batch: merged_order.extend(c.bead_id for c in batch)
        )
        cfg = _make_cfg(batch_max=1)
        queue = [new, old]  # insertion order favors 'new'; scoring must override

        refine(queue, runners, cfg, now=1000.0)
        assert merged_order[0] == "fblai-old", (
            f"old (longest-waiting) branch must merge first, got {merged_order}"
        )
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 7. refinery-attempts cap → escalate
# ===========================================================================

class TestAttemptsCap:
    def test_below_cap_reimplements_at_cap_escalates(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        cfg = _make_cfg(batch_max=8, refinery_attempts_max=2)

        # attempts 0,1 → reimplement ; attempts 2 (== cap) → exhausted
        for attempts, expected in [(0, "reimplement"), (1, "reimplement"), (2, "exhausted")]:
            wts: List[str] = []
            cand = _make_branch_candidate(
                repo, f"fblai-cap-{attempts}", filename=f"c{attempts}.txt",
                content="x\n", attempts=attempts, worktrees=wts,
            )
            runners = _make_refinery_runners(repo, verify_cmd_fn=lambda cmd: 1)
            outcomes = refine([cand], runners, cfg, now=1000.0)
            assert outcomes[0].kind == expected, (
                f"attempts={attempts} expected {expected}, got {outcomes[0].kind}"
            )
            _cleanup_worktrees(repo, wts)


# ===========================================================================
# 8. single-writer — merge_batch/git_reset never from a worker thread
# ===========================================================================

class TestSingleWriter:
    def test_refine_runs_on_caller_thread_worker_merge_is_violation(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []

        def _guard(_batch: List[MergeCandidate]) -> None:
            name = threading.current_thread().name
            assert "ThreadPoolExecutor" not in name, (
                f"merge_batch called from worker thread {name!r} — single-writer violation"
            )

        cand = _make_branch_candidate(repo, "fblai-sw",
                                      filename="sw.txt", content="x\n", worktrees=wts)
        runners = _make_refinery_runners(repo, merge_spy=_guard)
        cfg = _make_cfg(batch_max=8)

        # Main-thread refine: guard passes.
        outcomes = refine([cand], runners, cfg, now=1000.0)
        assert outcomes[0].kind == "merged"

        # A worker thread invoking the merge seam must trip the guard.
        cand2 = _make_branch_candidate(repo, "fblai-sw2",
                                       filename="sw2.txt", content="x\n", worktrees=wts)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(runners.merge_batch, [cand2])
            with pytest.raises(AssertionError):
                fut.result()
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 9. merge-slot serialization — _MERGE_LOCK held during the batch cycle
# ===========================================================================

class TestMergeSlot:
    def test_merge_lock_held_during_merge_batch(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        observed = []

        def _observe(_batch: List[MergeCandidate]) -> None:
            observed.append(_MERGE_LOCK.locked())

        cand = _make_branch_candidate(repo, "fblai-slot",
                                      filename="slot.txt", content="x\n", worktrees=wts)
        runners = _make_refinery_runners(repo, merge_spy=_observe)
        cfg = _make_cfg(batch_max=8)

        assert not _MERGE_LOCK.locked()
        refine([cand], runners, cfg, now=1000.0)
        assert observed == [True], "merge slot (_MERGE_LOCK) must be held during merge_batch"
        assert not _MERGE_LOCK.locked(), "merge slot released after the cycle"
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# 10. rollback atomicity — red batch leaves HEAD == snapshot
# ===========================================================================

class TestRollbackAtomicity:
    def test_red_batch_restores_head(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)
        wts: List[str] = []
        cands = [
            _make_branch_candidate(repo, f"fblai-rb-{i}",
                                   filename=f"rb{i}.txt", content=f"{i}\n",
                                   worktrees=wts)
            for i in range(2)
        ]
        snap = _head(repo)
        # V always red → whole batch rolled back, then each bisected single
        # branch also red → all reimplement, nothing lands.
        runners = _make_refinery_runners(repo, verify_cmd_fn=lambda cmd: 1)
        cfg = _make_cfg(batch_max=8)

        outcomes = refine(cands, runners, cfg, now=1000.0)
        assert all(o.kind == "reimplement" for o in outcomes)
        assert _head(repo) == snap, "HEAD must be byte-identical to the pre-batch snapshot"
        for i in range(2):
            assert not (repo / f"rb{i}.txt").exists()
        _cleanup_worktrees(repo, wts)


# ===========================================================================
# Integration — run_mayor_loop with batch_max>1 drives the Refinery end-to-end
# ===========================================================================

class TestMayorLoopRefineryIntegration:
    def test_mayor_loop_batches_and_closes(self, tmp_path: Path) -> None:
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        closed: List[str] = []
        statuses: dict = {}
        lock = threading.Lock()

        beads = [
            {"id": f"fblai-int-{i}", "title": f"Task {i}", "priority": 2, "labels": []}
            for i in range(3)
        ]
        for b in beads:
            statuses[b["id"]] = "open"

        def _ready(_molecule: str) -> List[dict]:
            with lock:
                return [b for b in beads if statuses.get(b["id"]) == "open"]

        def _update(bead_id: str, status: str) -> None:
            with lock:
                statuses[bead_id] = status

        def _close(bead_id: str) -> None:
            with lock:
                statuses[bead_id] = "closed"
                closed.append(bead_id)

        def _create(bead_id: str):
            safe = bead_id.replace("/", "_")
            branch = f"mayor/{bead_id}"
            wt_dir = str(repo.parent / f"int-wt-{safe}-{threading.get_ident()}")
            r = _git_ok(repo, "worktree", "add", "-b", branch, wt_dir, "HEAD")
            if r.returncode != 0:
                return None
            return (wt_dir, branch)

        def _teardown(worktree_path: str, branch_name: str) -> None:
            _git_ok(repo, "worktree", "remove", "--force", worktree_path)
            if branch_name:
                _git_ok(repo, "branch", "-D", branch_name)

        def _dispatch_with_cwd(prompt: str, model: str, timeout_s: int, cwd):
            # Each worker writes a distinct file in its worktree and commits.
            if cwd:
                wt = Path(cwd)
                fname = f"int_{Path(cwd).name}.txt"
                (wt / fname).write_text("done\n")
                _git(wt, "add", fname)
                _git(wt, "commit", "-m", f"impl {fname}")
            return {"tokens": 5, "output": "done"}

        max_batch = [0]

        def _merge_batch(batch: List[MergeCandidate]) -> bool:
            max_batch[0] = max(max_batch[0], len(batch))
            for c in batch:
                r = _git_ok(repo, "merge", "--no-ff", c.branch_name,
                            "-m", f"Mayor: merge {c.branch_name}")
                if r.returncode != 0:
                    return False
            return True

        runners = Runners(
            beads_ready=_ready,
            beads_close=_close,
            beads_update=_update,
            brain_recall=MagicMock(return_value=""),
            brain_capture=MagicMock(),
            dispatch=MagicMock(return_value={"tokens": 5, "output": "done"}),
            run_verify=MagicMock(return_value=0),
            run_verify_in_cwd=lambda cmd, t, cwd: 0,
            worktree_create=_create,
            worktree_teardown=_teardown,
            merge_branch=lambda b: 0,
            merge_batch=_merge_batch,
            git_snapshot=lambda branch: _git(repo, "rev-parse", "HEAD").stdout.strip(),
            git_reset=lambda snap: (_git_ok(repo, "merge", "--abort"),
                                    _git_ok(repo, "reset", "--hard", snap)),
            beads_relabel=MagicMock(),
            dispatch_with_cwd=_dispatch_with_cwd,
            loop_state_path=tmp_path / "loop-state.json",
        )

        cfg = _make_cfg(
            molecule="epic:refinery-int", repo=str(repo), branch="main",
            verify_cmd="true", max_workers=3, batch_max=8, max_iterations=20,
            once=False,
        )

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == 3, f"expected 3 closed, got {summary.closed}"
        assert sorted(closed) == [b["id"] for b in beads]
        # At least one batch carried more than one branch (the Refinery batched).
        assert max_batch[0] >= 2, (
            f"expected the Refinery to batch ≥2 branches at once, peak={max_batch[0]}"
        )
        # All worker files landed on the working branch.
        merged_files = list(repo.glob("int_*.txt"))
        assert len(merged_files) == 3, f"expected 3 merged files, got {merged_files}"
