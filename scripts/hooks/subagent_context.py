#!/usr/bin/env python3
"""
Subagent Context Manager for Claude Code

Tracks the subagent execution stack to enable lineage tracking across nested subagent calls.
Uses a shared JSON file for cross-process state management with automatic expiration.

Context file: ~/.claude/logs/subagent_context.json
Auto-expires: 30 minutes of inactivity
"""

import os
import json
import time
import fcntl
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
from uuid import uuid4


# Configuration
CONTEXT_FILE_PATH = Path.home() / ".claude" / "logs" / "subagent_context.json"
CONTEXT_EXPIRY_SECONDS = 30 * 60  # 30 minutes


class SubagentContext:
    """Manages subagent execution context with stack-based lineage tracking"""

    def __init__(self, context_path: Optional[Path] = None):
        """
        Initialize the subagent context manager

        Args:
            context_path: Custom path for context file (defaults to ~/.claude/logs/subagent_context.json)
        """
        self.context_path = context_path or CONTEXT_FILE_PATH
        self.context_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_context(self) -> Dict[str, Any]:
        """Read context from file with file locking for safety"""
        default_context = {
            "stack": [],
            "last_updated": time.time(),
            "session_id": None
        }

        if not self.context_path.exists():
            return default_context

        try:
            with open(self.context_path, 'r', encoding='utf-8') as f:
                # Get shared lock for reading
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    context = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

                # Check for expiration
                last_updated = context.get("last_updated", 0)
                if time.time() - last_updated > CONTEXT_EXPIRY_SECONDS:
                    # Context expired, return fresh context
                    return default_context

                return context
        except (json.JSONDecodeError, IOError):
            return default_context

    def _write_context(self, context: Dict[str, Any]):
        """Write context to file with file locking for safety"""
        context["last_updated"] = time.time()

        try:
            with open(self.context_path, 'w', encoding='utf-8') as f:
                # Get exclusive lock for writing
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(context, f, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except IOError as e:
            # Log error but don't fail the operation
            self._log_error(f"Failed to write context: {e}")

    def _log_error(self, error_msg: str):
        """Log errors to a separate file"""
        try:
            error_file = self.context_path.parent / "subagent_context_errors.log"
            with open(error_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"{timestamp} ERROR: {error_msg}\n")
        except:
            pass

    def generate_subagent_id(self, subagent_type: str) -> str:
        """
        Generate a unique subagent ID

        Format: subagent-{type}-{short_uuid}
        Example: subagent-Explore-a1b2c3d4
        """
        short_uuid = str(uuid4())[:8]
        return f"subagent-{subagent_type}-{short_uuid}"

    def push_subagent(self, subagent_type: str, subagent_id: str,
                      task_description: str = "", session_id: str = None) -> Dict[str, Any]:
        """
        Push a new subagent onto the stack (called when Task tool spawns a subagent)

        Args:
            subagent_type: Type of subagent (e.g., "Explore", "Plan", "general-purpose")
            subagent_id: Unique identifier for this subagent instance
            task_description: Brief description of the task
            session_id: Session ID (to track across processes)

        Returns:
            Dict with parent info and lineage
        """
        context = self._read_context()
        stack = context.get("stack", [])

        # Update session ID if provided
        if session_id:
            context["session_id"] = session_id

        # Determine parent info
        parent_subagent_id = None
        parent_subagent_type = None
        if stack:
            parent = stack[-1]
            parent_subagent_id = parent.get("subagent_id")
            parent_subagent_type = parent.get("subagent_type")

        # Build lineage (list of ancestor types)
        lineage = [entry.get("subagent_type") for entry in stack]
        lineage.append(subagent_type)

        # Create new stack entry
        entry = {
            "subagent_type": subagent_type,
            "subagent_id": subagent_id,
            "task_description": task_description,
            "parent_subagent_id": parent_subagent_id,
            "parent_subagent_type": parent_subagent_type,
            "depth": len(stack) + 1,
            "lineage": lineage,
            "started_at": time.time()
        }

        stack.append(entry)
        context["stack"] = stack
        self._write_context(context)

        return {
            "subagent_type": subagent_type,
            "subagent_id": subagent_id,
            "task_description": task_description,
            "parent_subagent_id": parent_subagent_id,
            "lineage": lineage,
            "depth": len(stack)
        }

    def pop_subagent(self, subagent_id: str = None) -> Optional[Dict[str, Any]]:
        """
        Pop a subagent from the stack (called when subagent completes)

        Args:
            subagent_id: Optional - if provided, validates it matches the top of stack

        Returns:
            The popped entry, or None if stack is empty
        """
        context = self._read_context()
        stack = context.get("stack", [])

        if not stack:
            return None

        # If subagent_id provided, validate it matches
        if subagent_id:
            top = stack[-1]
            if top.get("subagent_id") != subagent_id:
                # Mismatch - log warning but proceed
                self._log_error(f"Stack mismatch: expected {top.get('subagent_id')}, got {subagent_id}")

        popped = stack.pop()
        context["stack"] = stack
        self._write_context(context)

        return popped

    def get_current_context(self) -> Dict[str, Any]:
        """
        Get the current subagent context for logging

        Returns:
            Dict with current executing subagent info, or empty dict if at root level
        """
        context = self._read_context()
        stack = context.get("stack", [])

        if not stack:
            return {
                "is_subagent": False,
                "subagent_depth": 0,
                "subagent_lineage": []
            }

        current = stack[-1]
        return {
            "is_subagent": True,
            "executing_subagent": current.get("subagent_type"),
            "executing_subagent_id": current.get("subagent_id"),
            "subagent_depth": current.get("depth", len(stack)),
            "subagent_lineage": current.get("lineage", []),
            "parent_subagent_id": current.get("parent_subagent_id"),
            "task_description": current.get("task_description")
        }

    def get_lineage(self) -> List[str]:
        """Get the current subagent lineage (list of types from root to current)"""
        context = self._read_context()
        stack = context.get("stack", [])
        return [entry.get("subagent_type") for entry in stack]

    def get_depth(self) -> int:
        """Get the current subagent depth (0 = root level)"""
        context = self._read_context()
        return len(context.get("stack", []))

    def clear(self):
        """Clear the entire subagent stack (useful for session reset)"""
        context = {
            "stack": [],
            "last_updated": time.time(),
            "session_id": None
        }
        self._write_context(context)

    def touch(self):
        """Update the last_updated timestamp to prevent expiration during long operations"""
        context = self._read_context()
        self._write_context(context)


# Singleton instance
_context_instance = None


def get_context() -> SubagentContext:
    """Get a singleton context manager instance"""
    global _context_instance
    if _context_instance is None:
        _context_instance = SubagentContext()
    return _context_instance


def push_subagent(subagent_type: str, subagent_id: str,
                  task_description: str = "", session_id: str = None) -> Dict[str, Any]:
    """Convenience function to push a subagent onto the stack"""
    return get_context().push_subagent(subagent_type, subagent_id, task_description, session_id)


def pop_subagent(subagent_id: str = None) -> Optional[Dict[str, Any]]:
    """Convenience function to pop a subagent from the stack"""
    return get_context().pop_subagent(subagent_id)


def get_current_context() -> Dict[str, Any]:
    """Convenience function to get current subagent context"""
    return get_context().get_current_context()


def generate_subagent_id(subagent_type: str) -> str:
    """Convenience function to generate a subagent ID"""
    return get_context().generate_subagent_id(subagent_type)


if __name__ == "__main__":
    # Test the subagent context manager
    import sys

    ctx = SubagentContext()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "push" and len(sys.argv) >= 3:
            subagent_type = sys.argv[2]
            task_desc = sys.argv[3] if len(sys.argv) > 3 else ""
            subagent_id = generate_subagent_id(subagent_type)
            result = ctx.push_subagent(subagent_type, subagent_id, task_desc)
            print(f"Pushed: {json.dumps(result, indent=2)}")

        elif cmd == "pop":
            result = ctx.pop_subagent()
            print(f"Popped: {json.dumps(result, indent=2) if result else 'None'}")

        elif cmd == "status":
            result = ctx.get_current_context()
            print(f"Current context: {json.dumps(result, indent=2)}")

        elif cmd == "clear":
            ctx.clear()
            print("Context cleared")

        else:
            print("Usage: python subagent_context.py [push <type> [desc] | pop | status | clear]")
    else:
        # Demo
        print("=== Subagent Context Demo ===")

        # Clear any existing context
        ctx.clear()
        print("\n1. Starting fresh (cleared context)")
        print(f"   Current: {ctx.get_current_context()}")

        # Simulate a Plan agent spawning
        print("\n2. Spawning Plan agent...")
        plan_id = ctx.generate_subagent_id("Plan")
        result = ctx.push_subagent("Plan", plan_id, "Design implementation plan")
        print(f"   Pushed: {result}")

        # Plan spawns Explore
        print("\n3. Plan spawns Explore agent...")
        explore_id = ctx.generate_subagent_id("Explore")
        result = ctx.push_subagent("Explore", explore_id, "Find error handlers")
        print(f"   Pushed: {result}")

        # Current context
        print("\n4. Current context (while in Explore):")
        print(f"   {ctx.get_current_context()}")

        # Explore completes
        print("\n5. Explore completes...")
        popped = ctx.pop_subagent(explore_id)
        print(f"   Popped: {popped}")

        # Back to Plan context
        print("\n6. Back to Plan context:")
        print(f"   {ctx.get_current_context()}")

        # Plan completes
        print("\n7. Plan completes...")
        popped = ctx.pop_subagent(plan_id)
        print(f"   Popped: {popped}")

        # Back to root
        print("\n8. Back to root level:")
        print(f"   {ctx.get_current_context()}")
