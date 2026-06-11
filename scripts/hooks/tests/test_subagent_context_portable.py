"""
test_subagent_context_portable.py — Verify subagent_context.py runs on
platforms where fcntl is absent (Windows-native Python).

The test monkey-patches sys.modules to make fcntl unavailable, then
re-imports the module under test so the ImportError path executes. It
asserts:
  1. The module imports without raising ImportError.
  2. The three lock helpers (_lock_sh, _lock_ex, _lock_un) are callable.
  3. Calling them with a dummy fd does not raise.
  4. Full lifecycle (push, get_context, pop, clear) works end-to-end when
     fcntl is absent — i.e. the no-op shim does not break any logic.
"""

import importlib
import json
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_without_fcntl() -> ModuleType:
    """
    Import scripts/hooks/subagent_context.py with fcntl hidden from
    sys.modules so the ImportError branch executes.

    Returns the freshly-imported module object.
    """
    hooks_dir = Path(__file__).resolve().parent.parent
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))

    # Remove any cached version of the module so we re-execute its top level.
    for key in list(sys.modules.keys()):
        if "subagent_context" in key:
            del sys.modules[key]

    # Temporarily hide fcntl so the except ImportError branch fires.
    saved_fcntl = sys.modules.pop("fcntl", None)
    # Also block the `import fcntl as _fcntl` form by injecting a sentinel
    # that raises ImportError when imported.
    sys.modules["fcntl"] = None  # type: ignore[assignment]  # None triggers ImportError on `import fcntl`

    try:
        import subagent_context as mod
        return mod
    finally:
        # Restore original state regardless of success/failure.
        if saved_fcntl is not None:
            sys.modules["fcntl"] = saved_fcntl
        else:
            sys.modules.pop("fcntl", None)


# ---------------------------------------------------------------------------
# Tests: module imports cleanly without fcntl
# ---------------------------------------------------------------------------


class TestFcntlAbsent:
    """Verify that the no-op shim activates when fcntl is unavailable."""

    def setup_method(self):
        self.mod = _import_without_fcntl()

    def test_module_imports_without_error(self):
        """The module must be importable on systems without fcntl."""
        assert self.mod is not None

    def test_lock_helpers_are_callable(self):
        """All three lock helpers must exist and be callable."""
        assert callable(self.mod._lock_sh)
        assert callable(self.mod._lock_ex)
        assert callable(self.mod._lock_un)

    def test_lock_helpers_accept_dummy_fd(self):
        """No-op helpers must not raise even with an arbitrary fd value."""
        for helper in (self.mod._lock_sh, self.mod._lock_ex, self.mod._lock_un):
            # fd=-1 would normally be invalid for fcntl; the no-op must ignore it.
            helper(-1)

    def test_lock_helpers_noop_return_none(self):
        """No-op helpers must return None (no side effects)."""
        assert self.mod._lock_sh(0) is None
        assert self.mod._lock_ex(0) is None
        assert self.mod._lock_un(0) is None


# ---------------------------------------------------------------------------
# Tests: end-to-end lifecycle with no fcntl (using a temp file path)
# ---------------------------------------------------------------------------


class TestLifecycleWithoutFcntl:
    """Verify push/pop/get_context/clear work correctly when fcntl is absent."""

    def setup_method(self):
        self.mod = _import_without_fcntl()
        self.tmp_dir = tempfile.mkdtemp()
        self.ctx_path = Path(self.tmp_dir) / "subagent_context.json"
        self.ctx = self.mod.SubagentContext(context_path=self.ctx_path)

    def test_fresh_context_is_empty(self):
        result = self.ctx.get_current_context()
        assert result["is_subagent"] is False
        assert result["subagent_depth"] == 0
        assert result["subagent_lineage"] == []

    def test_push_creates_entry(self):
        agent_id = self.ctx.generate_subagent_id("Plan")
        pushed = self.ctx.push_subagent("Plan", agent_id, "test task")
        assert pushed["subagent_type"] == "Plan"
        assert pushed["subagent_id"] == agent_id
        assert pushed["depth"] == 1
        assert pushed["parent_subagent_id"] is None

    def test_get_context_after_push(self):
        agent_id = self.ctx.generate_subagent_id("Explore")
        self.ctx.push_subagent("Explore", agent_id, "explore files")
        ctx = self.ctx.get_current_context()
        assert ctx["is_subagent"] is True
        assert ctx["executing_subagent"] == "Explore"
        assert ctx["subagent_depth"] == 1

    def test_nested_push_tracks_lineage(self):
        plan_id = self.ctx.generate_subagent_id("Plan")
        explore_id = self.ctx.generate_subagent_id("Explore")
        self.ctx.push_subagent("Plan", plan_id, "plan")
        pushed = self.ctx.push_subagent("Explore", explore_id, "explore")
        assert pushed["depth"] == 2
        assert pushed["parent_subagent_id"] == plan_id
        assert "Plan" in pushed["lineage"]
        assert "Explore" in pushed["lineage"]

    def test_pop_removes_top(self):
        agent_id = self.ctx.generate_subagent_id("Implementer")
        self.ctx.push_subagent("Implementer", agent_id, "implement")
        popped = self.ctx.pop_subagent(agent_id)
        assert popped is not None
        assert popped["subagent_type"] == "Implementer"
        # Stack should be empty now
        assert self.ctx.get_depth() == 0

    def test_clear_empties_stack(self):
        for i in range(3):
            aid = self.ctx.generate_subagent_id(f"Agent{i}")
            self.ctx.push_subagent(f"Agent{i}", aid, "")
        assert self.ctx.get_depth() == 3
        self.ctx.clear()
        assert self.ctx.get_depth() == 0

    def test_context_file_written_as_valid_json(self):
        agent_id = self.ctx.generate_subagent_id("Tester")
        self.ctx.push_subagent("Tester", agent_id, "write test")
        assert self.ctx_path.exists()
        with open(self.ctx_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "stack" in data
        assert len(data["stack"]) == 1

    def test_expiry_returns_fresh_context(self):
        """Context older than CONTEXT_EXPIRY_SECONDS should be treated as fresh."""
        old_context = {
            "stack": [{"subagent_type": "OldAgent", "subagent_id": "x", "depth": 1,
                        "lineage": ["OldAgent"], "started_at": 0.0}],
            # Set last_updated to a time far in the past.
            "last_updated": time.time() - (self.mod.CONTEXT_EXPIRY_SECONDS + 10),
            "session_id": None,
        }
        with open(self.ctx_path, "w", encoding="utf-8") as fh:
            json.dump(old_context, fh)

        # The expired context should be discarded; depth should be 0.
        assert self.ctx.get_depth() == 0

    def test_pop_on_empty_stack_returns_none(self):
        result = self.ctx.pop_subagent()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: verify the real module (with fcntl if available) still works
# ---------------------------------------------------------------------------


class TestLifecycleWithFcntl:
    """
    Confirm the module works correctly on Unix (fcntl available) too.
    On Windows this suite will be skipped automatically since the test
    imports the module normally and fcntl may be absent.
    """

    def setup_method(self):
        # Import normally — fcntl will be used if available.
        hooks_dir = Path(__file__).resolve().parent.parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import subagent_context
        self.mod = subagent_context
        self.tmp_dir = tempfile.mkdtemp()
        self.ctx_path = Path(self.tmp_dir) / "subagent_context.json"
        self.ctx = self.mod.SubagentContext(context_path=self.ctx_path)

    def test_push_and_pop_roundtrip(self):
        aid = self.ctx.generate_subagent_id("RoundTrip")
        self.ctx.push_subagent("RoundTrip", aid, "round trip")
        popped = self.ctx.pop_subagent(aid)
        assert popped["subagent_type"] == "RoundTrip"
        assert self.ctx.get_depth() == 0

    def test_get_lineage_reflects_stack(self):
        types = ["Alpha", "Beta", "Gamma"]
        ids = []
        for t in types:
            aid = self.ctx.generate_subagent_id(t)
            ids.append(aid)
            self.ctx.push_subagent(t, aid, "")
        lineage = self.ctx.get_lineage()
        assert lineage == types
