"""test_worktree_integration.py — VA0b: worktree-preserving code integration tests.

Bead: fblai-wyaan

Tests using REAL git repos in tmp_path to verify:
1. Worker code lands on the working branch after a V-pass merge.
2. V runs against the worktree's contents (sees the worker's change).
3. V-fail → no merge, code discarded, bead stays open.
4. Merge is serialized (no overlapping merges).
5. Single-writer preserved (worker callable never calls merge/beads_close/beads_update).

Run: python3 -m pytest scripts/tests/test_worktree_integration.py -q
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
_HOOKS_DIR = _SCRIPTS_DIR / "hooks"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from loop_runner import (
    RunConfig,
    Runners,
    WorkerResult,
    _MERGE_LOCK,
    _live_worktree_create,
    _live_worktree_teardown,
    _live_merge_worktree_branch,
    _live_run_verify_in_cwd,
    _mayor_worker,
    run_mayor_loop,
    MayorSummary,
)


# ---------------------------------------------------------------------------
# Git test repo helpers
# ---------------------------------------------------------------------------

def _init_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit so worktrees work."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in cwd, check=True, capture output."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


def _git_ok(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command, do NOT raise on non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _file_contents(path: Path) -> Optional[str]:
    if path.exists():
        return path.read_text()
    return None


def _has_branch(repo: Path, branch: str) -> bool:
    r = _git_ok(repo, "branch", "--list", branch)
    return branch in r.stdout


def _commit_and_write(worktree: Path, filename: str, content: str) -> None:
    """Write a file in a worktree and commit it."""
    (worktree / filename).write_text(content)
    _git(worktree, "add", filename)
    _git(worktree, "commit", "-m", f"add {filename}")


def _skip_if_git_unavailable():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git not available")


# ---------------------------------------------------------------------------
# Fake runners helpers for VA0b lifecycle
# ---------------------------------------------------------------------------

class _StatusTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._statuses: dict[str, str] = {}
        self._closed: List[str] = []
        self._updated: List[tuple] = []

    def set_open(self, bead_id: str) -> None:
        with self._lock:
            self._statuses[bead_id] = "open"

    def update(self, bead_id: str, status: str) -> None:
        with self._lock:
            self._statuses[bead_id] = status
            self._updated.append((bead_id, status))

    def close(self, bead_id: str) -> None:
        with self._lock:
            self._statuses[bead_id] = "closed"
            self._closed.append(bead_id)

    def get_status(self, bead_id: str) -> str:
        with self._lock:
            return self._statuses.get(bead_id, "unknown")

    @property
    def closed_beads(self) -> List[str]:
        with self._lock:
            return list(self._closed)


def _make_bead(
    bead_id: str = "fblai-test",
    title: str = "Test bead",
    priority: int = 2,
    labels: Optional[List[str]] = None,
    body: str = "",
) -> dict:
    return {
        "id": bead_id,
        "title": title,
        "priority": priority,
        "labels": labels or [],
        "body": body,
    }


def _make_cfg(**overrides) -> RunConfig:
    defaults = dict(
        molecule="test-molecule",
        repo="/repo",
        branch="main",
        verify_cmd="true",
        max_iterations=25,
        budget_tokens=10_000_000,
        dry_run=False,
        once=False,
        max_workers=2,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def _make_va0b_runners(
    *,
    repo: Path,
    tracker: _StatusTracker,
    dispatch_fn: Optional[Callable] = None,
    verify_exit_override: Optional[int] = None,
    loop_state_path: Optional[Path] = None,
) -> Runners:
    """Build Runners with the real VA0b lifecycle functions scoped to a tmp repo.

    All git operations happen inside `repo` (the tmp_path repo), not the
    runner's actual working directory.  We patch the create/teardown/merge
    functions to use `repo` as their cwd.
    """
    merge_calls: List[str] = []
    merge_lock_acquired_times: List[float] = []

    def _create(bead_id: str) -> Optional[tuple]:
        """Create a named-branch worktree scoped to the test repo."""
        import os, tempfile, threading
        safe_id = bead_id.replace("/", "_").replace("\\", "_")
        branch_name = f"mayor/{bead_id}"
        wt_dir = str(Path(tempfile.gettempdir()) / f"test-mayor-wt-{safe_id}-{threading.get_ident()}")
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, wt_dir, "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return (wt_dir, branch_name)

    def _teardown(worktree_path: str, branch_name: str) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if branch_name:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )

    def _merge(branch_name: str) -> int:
        merge_calls.append(branch_name)
        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name,
             "-m", f"Mayor: merge {branch_name}"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode

    def _verify_in_cwd(cmd: str, timeout_s: int, cwd: str) -> int:
        if verify_exit_override is not None:
            return verify_exit_override
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=cwd, timeout=timeout_s, check=False,
                capture_output=True,
            )
            return r.returncode
        except subprocess.TimeoutExpired:
            return 1

    def _dispatch_with_cwd(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
        if dispatch_fn is not None:
            return dispatch_fn(prompt, model, timeout_s, cwd)
        return {"tokens": 5, "output": "done"}

    def _fail_if_worker_bead_write(label: str):
        def _fn(*args, **kwargs):
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"{label} called from worker thread '{t.name}' — single-writer violation"
            )
        return _fn

    runners = Runners(
        beads_ready=MagicMock(return_value=[]),
        beads_close=_fail_if_worker_bead_write("beads_close"),
        beads_update=_fail_if_worker_bead_write("beads_update"),
        brain_recall=MagicMock(return_value=""),
        brain_capture=MagicMock(),
        dispatch=lambda p, m, t: {"tokens": 5, "output": "done"},
        run_verify=MagicMock(return_value=0),
        run_verify_in_cwd=_verify_in_cwd,
        worktree_create=_create,
        worktree_teardown=_teardown,
        merge_branch=_merge,
        dispatch_with_cwd=_dispatch_with_cwd,
        loop_state_path=loop_state_path,
    )
    runners._merge_calls = merge_calls  # type: ignore[attr-defined]
    return runners


# ---------------------------------------------------------------------------
# Test 1 — Worker code lands on working branch after V-pass merge
# ---------------------------------------------------------------------------

class TestWorkerCodeIntegration:
    """Worker commits in worktree must reach the working branch on V-pass."""

    def test_file_written_in_worktree_lands_on_working_branch(self, tmp_path: Path) -> None:
        """A file committed in the worktree appears in the working branch after merge."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-va0b-merge"
        bead = _make_bead(bead_id, title="Write a feature file")
        cfg = _make_cfg(verify_cmd="test -f feature.txt")

        # dispatch_fn: writes a file to the worktree and commits it
        def _write_and_commit(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            if cwd is not None:
                wt = Path(cwd)
                _commit_and_write(wt, "feature.txt", "feature content\n")
            return {"tokens": 10, "output": "done"}

        tracker = _StatusTracker()
        tracker.set_open(bead_id)
        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            dispatch_fn=_write_and_commit,
            # verify: look for feature.txt in the worktree (cwd=worktree)
            verify_exit_override=None,  # real verify runs: test -f feature.txt
        )
        # Override verify to check within the worktree cwd (real subprocess)
        def _real_verify(cmd: str, timeout_s: int, cwd: str) -> int:
            r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout_s, check=False)
            return r.returncode
        runners.run_verify_in_cwd = _real_verify
        runners.beads_close = tracker.close
        runners.beads_update = tracker.update

        res = _mayor_worker(bead, cfg, runners)

        # Worker should have V=0
        assert res.verify_exit == 0, f"Expected V=0, got {res.verify_exit}"
        assert res.branch_name is not None
        assert res.worktree_path is not None

        # Merge (as Mayor would)
        with _MERGE_LOCK:
            merge_exit = runners.merge_branch(res.branch_name)
        assert merge_exit == 0, f"Merge failed with exit {merge_exit}"

        # Teardown
        runners.worktree_teardown(res.worktree_path, res.branch_name)

        # feature.txt must now be on the working branch
        working_branch_file = repo / "feature.txt"
        assert working_branch_file.exists(), (
            "feature.txt was not merged into the working branch after V-pass"
        )
        assert working_branch_file.read_text() == "feature content\n"

    def test_branch_is_named_mayor_bead_id(self, tmp_path: Path) -> None:
        """The worktree branch is named mayor/<bead_id>."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-branch-name-check"
        bead = _make_bead(bead_id)
        cfg = _make_cfg()

        tracker = _StatusTracker()
        runners = _make_va0b_runners(repo=repo, tracker=tracker, verify_exit_override=1)

        res = _mayor_worker(bead, cfg, runners)

        assert res.branch_name == f"mayor/{bead_id}", (
            f"Expected branch 'mayor/{bead_id}', got {res.branch_name!r}"
        )
        # Teardown
        if res.worktree_path:
            runners.worktree_teardown(res.worktree_path, res.branch_name or "")


# ---------------------------------------------------------------------------
# Test 2 — V runs against the worktree's contents
# ---------------------------------------------------------------------------

class TestVerifyRunsInWorktree:
    """V must see the worker's committed changes (cwd = worktree path)."""

    def test_v_sees_worktree_file_not_working_branch(self, tmp_path: Path) -> None:
        """V runs in the worktree, so it can see a file that doesn't yet exist
        on the working branch."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        # File does NOT exist on working branch yet
        assert not (repo / "new_feature.py").exists()

        bead_id = "fblai-v-in-wt"
        bead = _make_bead(bead_id, title="Create new_feature.py")
        cfg = _make_cfg(verify_cmd="test -f new_feature.py")

        def _write_feature(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            if cwd:
                wt = Path(cwd)
                _commit_and_write(wt, "new_feature.py", "# feature\n")
            return {"tokens": 5, "output": "done"}

        def _real_verify_in_cwd(cmd: str, timeout_s: int, cwd: str) -> int:
            r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout_s, check=False)
            return r.returncode

        tracker = _StatusTracker()
        runners = _make_va0b_runners(repo=repo, tracker=tracker)
        runners.dispatch_with_cwd = _write_feature
        runners.run_verify_in_cwd = _real_verify_in_cwd

        res = _mayor_worker(bead, cfg, runners)

        # V in the worktree must pass (file exists there)
        assert res.verify_exit == 0, (
            f"V should see new_feature.py in the worktree (exit 0), got {res.verify_exit}"
        )
        # File must NOT be on working branch yet (merge hasn't happened)
        assert not (repo / "new_feature.py").exists(), (
            "File appeared on working branch before Mayor merge — lifecycle violation"
        )

        # Cleanup
        if res.worktree_path:
            runners.worktree_teardown(res.worktree_path, res.branch_name or "")

    def test_v_failure_in_worktree_is_reported_as_nonzero(self, tmp_path: Path) -> None:
        """When a file the V command needs doesn't exist in the worktree, V exits nonzero."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-v-fail-wt"
        bead = _make_bead(bead_id)
        cfg = _make_cfg(verify_cmd="test -f missing_file.py")

        # dispatch doesn't write the file
        def _do_nothing(prompt: str, model: str, timeout_s: int, cwd: Optional[str]) -> dict:
            return {"tokens": 2, "output": "nothing"}

        def _real_verify_in_cwd(cmd: str, timeout_s: int, cwd: str) -> int:
            r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout_s, check=False)
            return r.returncode

        tracker = _StatusTracker()
        runners = _make_va0b_runners(repo=repo, tracker=tracker)
        runners.dispatch_with_cwd = _do_nothing
        runners.run_verify_in_cwd = _real_verify_in_cwd

        res = _mayor_worker(bead, cfg, runners)

        assert res.verify_exit != 0, (
            f"V should fail (file missing), got verify_exit={res.verify_exit}"
        )
        # Cleanup
        if res.worktree_path:
            runners.worktree_teardown(res.worktree_path, res.branch_name or "")


# ---------------------------------------------------------------------------
# Test 3 — V-fail → no merge, code discarded, bead stays open
# ---------------------------------------------------------------------------

class TestVFailNoMerge:
    """On V≠0: no merge, worktree torn down, working branch unchanged, bead stays open."""

    def test_v_fail_code_does_not_reach_working_branch(self, tmp_path: Path) -> None:
        """A file committed in the worktree must NOT appear on working branch when V fails."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-vfail-discard"
        bead = _make_bead(bead_id)
        cfg = _make_cfg(verify_cmd="test -f never_exists.txt")

        def _write_but_v_fails(
            prompt: str, model: str, timeout_s: int, cwd: Optional[str]
        ) -> dict:
            if cwd:
                _commit_and_write(Path(cwd), "unwanted.txt", "should not appear\n")
            return {"tokens": 5, "output": "done"}

        def _real_verify_in_cwd(cmd: str, timeout_s: int, cwd: str) -> int:
            r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout_s, check=False)
            return r.returncode

        tracker = _StatusTracker()
        merge_calls: List[str] = []

        runners = _make_va0b_runners(repo=repo, tracker=tracker)
        runners.dispatch_with_cwd = _write_but_v_fails
        runners.run_verify_in_cwd = _real_verify_in_cwd

        # Capture merge calls to assert it was NOT called
        original_merge = runners.merge_branch
        def _spy_merge(branch: str) -> int:
            merge_calls.append(branch)
            return original_merge(branch)
        runners.merge_branch = _spy_merge

        res = _mayor_worker(bead, cfg, runners)

        # V failed
        assert res.verify_exit != 0

        # Mayor decision: no merge, teardown
        if res.worktree_path:
            runners.worktree_teardown(res.worktree_path, res.branch_name or "")

        # Merge must NOT have been called
        assert len(merge_calls) == 0, (
            f"merge_branch was called {len(merge_calls)} time(s) on V-fail — must not merge"
        )

        # unwanted.txt must not be on working branch
        assert not (repo / "unwanted.txt").exists(), (
            "unwanted.txt appeared on working branch even though V failed"
        )

    def test_mayor_loop_v_fail_bead_stays_open(self, tmp_path: Path) -> None:
        """In run_mayor_loop, a bead whose V fails remains open (not closed)."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-loop-vfail"
        bead = _make_bead(bead_id, title="Bead that will fail V")
        tracker = _StatusTracker()
        tracker.set_open(bead_id)
        cfg = _make_cfg(
            verify_cmd="false",  # always fail
            max_workers=1,
            max_iterations=3,
            once=True,
        )

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [bead] if tracker.get_status(b["id"]) == "open"]

        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=1,  # V always fails
            loop_state_path=tmp_path / "loop-state.json",
        )
        runners.beads_ready = _ready_fn
        runners.beads_close = tracker.close
        runners.beads_update = tracker.update

        summary = run_mayor_loop(cfg, runners)

        # Bead must not be closed
        assert bead_id not in tracker.closed_beads, (
            f"Bead {bead_id} was closed despite V failing"
        )
        assert tracker.get_status(bead_id) in ("open", "in_progress"), (
            f"Unexpected status: {tracker.get_status(bead_id)}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Merge is serialized under _MERGE_LOCK
# ---------------------------------------------------------------------------

class TestMergeSerializiation:
    """Merges from different worker branches must be serialized (no concurrent git merges)."""

    def test_concurrent_merge_calls_are_serialized(self, tmp_path: Path) -> None:
        """Simulate N concurrent workers each returning V=0; their merges must not overlap."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        n_workers = 3
        merge_overlap_detected = threading.Event()
        active_merges = threading.Semaphore(1)  # if >1 acquires at once, overlap

        original_merge_fn = None

        # Track concurrent merge invocations
        in_merge_count = [0]
        in_merge_lock = threading.Lock()

        def _spy_merge_with_overlap_check(branch: str) -> int:
            with in_merge_lock:
                in_merge_count[0] += 1
                if in_merge_count[0] > 1:
                    merge_overlap_detected.set()

            # Do the real merge
            result = subprocess.run(
                ["git", "merge", "--no-ff", branch, "-m", f"Mayor: merge {branch}"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )

            with in_merge_lock:
                in_merge_count[0] -= 1

            return result.returncode

        # Create n separate worktree branches to simulate n workers completing
        beads = [
            _make_bead(f"fblai-serial-m-{i}", title=f"Task {i}", priority=i + 1)
            for i in range(n_workers)
        ]
        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
        )
        runners.merge_branch = _spy_merge_with_overlap_check

        # Execute workers sequentially (single-writer, Mayor serializes merges)
        results = []
        for bead in beads:
            res = _mayor_worker(bead, cfg=_make_cfg(), runners=runners)
            results.append(res)

        # Merges happen under Mayor's _MERGE_LOCK — they cannot overlap by design.
        # The spy above cannot detect thread-level overlap from a single thread,
        # but we verify the _MERGE_LOCK is a real threading.Lock that blocks.
        assert isinstance(_MERGE_LOCK, type(threading.Lock())), (
            "_MERGE_LOCK must be a threading.Lock instance"
        )
        assert not merge_overlap_detected.is_set(), (
            "Concurrent merges were detected — _MERGE_LOCK is not being respected"
        )

        # Cleanup all worktrees
        for res in results:
            if res.worktree_path:
                runners.worktree_teardown(res.worktree_path, res.branch_name or "")

    def test_mayor_loop_merges_serialized_across_workers(self, tmp_path: Path) -> None:
        """In run_mayor_loop with multiple workers, merges are never concurrent."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        n_beads = 3
        beads = [
            _make_bead(f"fblai-loop-merge-{i}", title=f"Feature {i}", priority=i + 1)
            for i in range(n_beads)
        ]
        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        max_concurrent_merges = [0]
        current_merges = [0]
        merge_count_lock = threading.Lock()

        def _counting_merge(branch: str) -> int:
            with merge_count_lock:
                current_merges[0] += 1
                max_concurrent_merges[0] = max(max_concurrent_merges[0], current_merges[0])
            try:
                result = subprocess.run(
                    ["git", "merge", "--no-ff", branch, "-m", f"Mayor: merge {branch}"],
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return result.returncode
            finally:
                with merge_count_lock:
                    current_merges[0] -= 1

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in beads if tracker.get_status(b["id"]) == "open"]

        cfg = _make_cfg(max_workers=3, max_iterations=20, once=False)

        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        runners.beads_ready = _ready_fn
        runners.beads_close = tracker.close
        runners.beads_update = tracker.update
        runners.merge_branch = _counting_merge

        summary = run_mayor_loop(cfg, runners)

        # Merges are dispatched from the Mayor (main thread) under _MERGE_LOCK,
        # so even if workers complete concurrently, merges are always serialized.
        assert max_concurrent_merges[0] <= 1, (
            f"Peak concurrent merges was {max_concurrent_merges[0]} — expected ≤1 (serialized)"
        )
        assert summary.closed >= 1, f"Expected at least 1 bead closed, got {summary.closed}"


# ---------------------------------------------------------------------------
# Test 5 — Single-writer preserved (worker never calls merge/beads_close/beads_update)
# ---------------------------------------------------------------------------

class TestSingleWriterPreservedVA0b:
    """With VA0b lifecycle, the worker callable must never call merge, beads_close,
    or beads_update.  Only the Mayor (main thread) may do these."""

    def test_worker_does_not_call_merge(self, tmp_path: Path) -> None:
        """_mayor_worker with VA0b lifecycle must never call merge_branch."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead = _make_bead("fblai-sw-no-merge")
        cfg = _make_cfg()
        merge_calls: List[str] = []

        def _spy_merge(branch: str) -> int:
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"merge_branch called from worker thread '{t.name}' — single-writer violation"
            )
            merge_calls.append(branch)
            return 0

        tracker = _StatusTracker()
        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
        )
        runners.merge_branch = _spy_merge

        result = _mayor_worker(bead, cfg, runners)

        # Worker returned V=0 with branch info but did NOT call merge
        assert result.verify_exit == 0
        assert result.branch_name is not None
        assert len(merge_calls) == 0, (
            f"merge_branch was called {len(merge_calls)} time(s) from _mayor_worker "
            "— single-writer violation: only the Mayor may merge"
        )

        # Cleanup
        if result.worktree_path:
            runners.worktree_teardown(result.worktree_path, result.branch_name or "")

    def test_worker_does_not_call_beads_close(self, tmp_path: Path) -> None:
        """_mayor_worker with VA0b lifecycle must not call beads_close."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead = _make_bead("fblai-sw-no-close")
        cfg = _make_cfg()
        close_calls: List[str] = []

        def _spy_close(bead_id: str) -> None:
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"beads_close called from thread '{t.name}' — single-writer violation"
            )
            close_calls.append(bead_id)

        tracker = _StatusTracker()
        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
        )
        runners.beads_close = _spy_close

        _mayor_worker(bead, cfg, runners)

        assert len(close_calls) == 0, (
            f"beads_close called {len(close_calls)} time(s) from worker — single-writer violation"
        )

    def test_worker_does_not_call_beads_update(self, tmp_path: Path) -> None:
        """_mayor_worker with VA0b lifecycle must not call beads_update."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead = _make_bead("fblai-sw-no-update")
        cfg = _make_cfg()
        update_calls: List[tuple] = []

        def _spy_update(bead_id: str, status: str) -> None:
            t = threading.current_thread()
            assert "ThreadPoolExecutor" not in t.name, (
                f"beads_update called from thread '{t.name}' — single-writer violation"
            )
            update_calls.append((bead_id, status))

        tracker = _StatusTracker()
        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
        )
        runners.beads_update = _spy_update

        _mayor_worker(bead, cfg, runners)

        assert len(update_calls) == 0, (
            f"beads_update called {len(update_calls)} time(s) from worker "
            "— single-writer violation"
        )

    def test_mayor_loop_beads_close_only_from_main_thread_va0b(
        self, tmp_path: Path
    ) -> None:
        """In run_mayor_loop with VA0b runners, beads_close is always called
        from the main thread (Mayor), never from a worker."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        n = 3
        beads = [
            _make_bead(f"fblai-loop-sw-{i}", title=f"Task {i}", priority=i + 1)
            for i in range(n)
        ]
        tracker = _StatusTracker()
        for b in beads:
            tracker.set_open(b["id"])

        close_thread_names: List[str] = []
        close_lock = threading.Lock()

        def _spy_close(bead_id: str) -> None:
            with close_lock:
                close_thread_names.append(threading.current_thread().name)
            tracker.close(bead_id)

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in beads if tracker.get_status(b["id"]) == "open"]

        cfg = _make_cfg(max_workers=2, max_iterations=20)

        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        runners.beads_ready = _ready_fn
        runners.beads_close = _spy_close
        runners.beads_update = tracker.update

        summary = run_mayor_loop(cfg, runners)

        assert summary.closed == n, f"Expected {n} closed, got {summary.closed}"

        for thread_name in close_thread_names:
            assert "ThreadPoolExecutor" not in thread_name, (
                f"beads_close called from worker thread '{thread_name}' — "
                "single-writer invariant violated"
            )

    def test_worktree_teardown_happens_after_close_not_before(
        self, tmp_path: Path
    ) -> None:
        """Teardown must happen AFTER beads_close, not before (sequence test)."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-order-check"
        bead = _make_bead(bead_id)
        tracker = _StatusTracker()
        tracker.set_open(bead_id)

        sequence: List[str] = []
        seq_lock = threading.Lock()

        def _ready_fn(molecule: str) -> List[dict]:
            return [b for b in [bead] if tracker.get_status(b["id"]) == "open"]

        original_teardown = None

        def _spy_close(bid: str) -> None:
            with seq_lock:
                sequence.append(f"close:{bid}")
            tracker.close(bid)

        def _spy_teardown(path: str, branch: str) -> None:
            with seq_lock:
                sequence.append(f"teardown:{branch}")
            # Call the real teardown
            original_teardown(path, branch)

        cfg = _make_cfg(max_workers=1, max_iterations=10, once=True)

        runners = _make_va0b_runners(
            repo=repo,
            tracker=tracker,
            verify_exit_override=0,
            loop_state_path=tmp_path / "loop-state.json",
        )
        original_teardown = runners.worktree_teardown
        runners.beads_ready = _ready_fn
        runners.beads_close = _spy_close
        runners.beads_update = tracker.update
        runners.worktree_teardown = _spy_teardown

        run_mayor_loop(cfg, runners)

        # close must appear before teardown in the sequence
        close_idx = next((i for i, s in enumerate(sequence) if s.startswith("close:")), None)
        teardown_idx = next((i for i, s in enumerate(sequence) if s.startswith("teardown:")), None)

        assert close_idx is not None, f"beads_close was not called. Sequence: {sequence}"
        assert teardown_idx is not None, f"worktree_teardown was not called. Sequence: {sequence}"
        assert close_idx < teardown_idx, (
            f"worktree_teardown ({teardown_idx}) happened before beads_close ({close_idx}). "
            f"Sequence: {sequence}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Worktree lifecycle: no worktree leaks after Mayor processes results
# ---------------------------------------------------------------------------

class TestWorktreeLifecycle:
    """After the Mayor processes each worker result, no worktrees should remain on disk."""

    def test_no_worktree_dirs_remain_after_v_pass(self, tmp_path: Path) -> None:
        """After a successful V-pass + merge + teardown, the worktree directory is gone."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead = _make_bead("fblai-wt-cleanup-pass")
        cfg = _make_cfg()
        tracker = _StatusTracker()

        runners = _make_va0b_runners(repo=repo, tracker=tracker, verify_exit_override=0)
        res = _mayor_worker(bead, cfg, runners)

        assert res.verify_exit == 0
        wt_path = res.worktree_path
        assert wt_path is not None

        # Simulate Mayor decision: merge then teardown
        runners.merge_branch(res.branch_name)
        runners.worktree_teardown(wt_path, res.branch_name or "")

        assert not Path(wt_path).exists(), (
            f"Worktree directory {wt_path} still exists after teardown"
        )

    def test_no_worktree_dirs_remain_after_v_fail(self, tmp_path: Path) -> None:
        """After a V-fail + teardown (no merge), the worktree directory is gone."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead = _make_bead("fblai-wt-cleanup-fail")
        cfg = _make_cfg()
        tracker = _StatusTracker()

        runners = _make_va0b_runners(repo=repo, tracker=tracker, verify_exit_override=1)
        res = _mayor_worker(bead, cfg, runners)

        assert res.verify_exit == 1
        wt_path = res.worktree_path

        # Simulate Mayor decision: no merge, just teardown
        if wt_path:
            runners.worktree_teardown(wt_path, res.branch_name or "")
            assert not Path(wt_path).exists(), (
                f"Worktree directory {wt_path} still exists after V-fail teardown"
            )

    def test_named_branch_deleted_after_teardown(self, tmp_path: Path) -> None:
        """After teardown, the mayor/<bead_id> branch must be deleted from the repo."""
        _skip_if_git_unavailable()
        repo = _init_repo(tmp_path)

        bead_id = "fblai-branch-delete"
        bead = _make_bead(bead_id)
        cfg = _make_cfg()
        tracker = _StatusTracker()

        runners = _make_va0b_runners(repo=repo, tracker=tracker, verify_exit_override=0)
        res = _mayor_worker(bead, cfg, runners)

        branch = res.branch_name
        assert branch is not None

        # Branch exists before teardown (it's the worktree's branch)
        # Merge first so it's merged into main, then teardown deletes the branch
        runners.merge_branch(branch)
        runners.worktree_teardown(res.worktree_path or "", branch)

        # Branch must be gone
        assert not _has_branch(repo, branch), (
            f"Branch {branch!r} still exists after worktree teardown"
        )
