#!/usr/bin/env python3
"""
ABOUTME: Beads hook integration for automatic bead creation.
ABOUTME: Creates beads from significant Claude Code operations.

Integrates with pre-tool-use.py and user-prompt-submit.py hooks to
automatically create beads in the global database (~/.claude/beads/).

Design decisions:
- Only creates beads for "significant" operations to avoid noise
- Uses global database (gzg- prefix) for cross-session visibility
- Labels beads with operation type and 'auto' for filtering
- Deduplicates by content hash to prevent duplicate beads
"""

import os
import re
import sys
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional

# Import secret redaction (fail silently if not available)
try:
    from redact_secrets import redact_secrets, redact_dict
except ImportError:
    # Fallback: no redaction if module unavailable
    def redact_secrets(text):
        return text
    def redact_dict(data, max_depth=10):
        return data

# Global beads location
GLOBAL_BEADS_DIR = Path.home() / ".claude" / "beads"
GLOBAL_PREFIX = "gzg"

# Content hash cache to prevent duplicates within session
# Limited to 1000 entries to prevent unbounded growth
_content_hash_cache: set = set()
_MAX_CACHE_SIZE = 1000

# Track if we've logged the import error (log once)
_import_error_logged = False


def _get_beads_path() -> Optional[Path]:
    """
    Find beads module path with fallbacks.

    Tries multiple locations to find the beads module:
    1. Environment variable BEADS_PATH
    2. Common installation paths
    3. Relative to this hook file
    """
    candidates = [
        # Environment variable override
        Path(os.environ.get('BEADS_PATH', '')) if os.environ.get('BEADS_PATH') else None,
        # Relative to hooks directory (works for both installed hooks and repo hooks)
        Path(__file__).resolve().parent.parent.parent / "scripts" if Path(__file__).resolve().parent.name == "hooks" else None,
        # Standard macOS location
        Path.home() / "Documents" / "optivai" / "optivai-claude-plugin" / "scripts",
        # Standard Windows location
        Path.home() / "optivai-claude-plugin" / "scripts",
        # Alternative locations
        Path.home() / ".claude" / "beads" / "scripts",
    ]

    for path in candidates:
        if path and path.exists() and (path / "beads").exists():
            return path
    return None


def _ensure_beads_in_path():
    """Add beads to sys.path if found."""
    global _import_error_logged

    beads_path = _get_beads_path()
    if beads_path and str(beads_path) not in sys.path:
        sys.path.insert(0, str(beads_path))
        return True
    elif not beads_path and not _import_error_logged:
        print("[beads_writer] Warning: Could not find beads module. Set BEADS_PATH env var.", file=sys.stderr)
        _import_error_logged = True
        return False
    return beads_path is not None


def _content_hash(content: str) -> str:
    """Generate hash of content for deduplication."""
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _add_to_cache(content_hash: str) -> bool:
    """
    Add hash to cache, return True if new (not duplicate).
    Clears cache if it exceeds max size.
    """
    global _content_hash_cache

    # Clear cache if too large to prevent unbounded growth
    if len(_content_hash_cache) >= _MAX_CACHE_SIZE:
        _content_hash_cache = set()

    if content_hash in _content_hash_cache:
        return False

    _content_hash_cache.add(content_hash)
    return True


def _get_beads_db():
    """Get or create global beads database."""
    global _import_error_logged

    if not _ensure_beads_in_path():
        return None

    try:
        from beads.storage import BeadsDatabase
        GLOBAL_BEADS_DIR.mkdir(parents=True, exist_ok=True)
        return BeadsDatabase(GLOBAL_BEADS_DIR, prefix=GLOBAL_PREFIX)
    except ImportError as e:
        if not _import_error_logged:
            print(f"[beads_writer] ImportError: {e}", file=sys.stderr)
            _import_error_logged = True
        return None
    except Exception as e:
        if not _import_error_logged:
            print(f"[beads_writer] Error creating database: {e}", file=sys.stderr)
            _import_error_logged = True
        return None


def _truncate(text: str, max_len: int = 100) -> str:
    """Truncate text to max length with ellipsis."""
    text = text or ''
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _is_significant_operation(operation: str, details: Dict[str, Any]) -> bool:
    """
    Determine if an operation is significant enough to create a bead.

    Significant operations:
    - write: Creating new files
    - edit: Modifying files (but not trivial edits)
    - task: Launching subagents
    - bash: Running commands (filtered)
    - user_prompt: User messages (filtered to meaningful ones)

    Filtered out:
    - read: Too frequent, low signal
    - glob/grep: Search operations, too noisy
    - Trivial bash commands (ls, pwd, etc.)
    """
    # Always significant
    always_significant = {'write', 'task'}
    if operation in always_significant:
        return True

    # Edit is significant if it's a real change
    if operation == 'edit':
        return True

    # Bash - filter out trivial commands
    if operation == 'bash':
        command = details.get('command') or ''

        # Trivial commands to filter out
        trivial_commands = {'ls', 'pwd', 'cd', 'echo', 'cat', 'head', 'tail', 'which', 'type'}
        parts = command.split()
        first_word = parts[0].lower() if parts else ''

        if first_word in trivial_commands:
            return False

        # Git informational commands (case-insensitive, handles flags)
        # Pattern: git [flags] (status|diff|log)
        git_info_pattern = r'^git\s+(?:--\S+\s+)*(?:status|diff|log)\b'
        if re.match(git_info_pattern, command, re.IGNORECASE):
            return False

        return True

    # User prompts - only significant if substantial
    if operation == 'user_prompt':
        prompt = details.get('prompt') or ''
        # Skip very short prompts (likely "yes", "ok", "continue")
        if len(prompt) < 20:
            return False
        return True

    return False


def _create_bead_title(operation: str, details: Dict[str, Any]) -> str:
    """Generate a meaningful title for the bead."""
    if operation == 'write':
        file_path = details.get('file_path') or 'unknown'
        return f"[write] Created {Path(file_path).name}"

    if operation == 'edit':
        file_path = details.get('file_path') or 'unknown'
        return f"[edit] Modified {Path(file_path).name}"

    if operation == 'task':
        subagent = details.get('subagent_type') or 'unknown'
        desc = details.get('description') or ''
        return f"[task:{subagent}] {_truncate(desc, 60)}"

    if operation == 'bash':
        command = details.get('command') or ''
        return f"[bash] {_truncate(command, 70)}"

    if operation == 'user_prompt':
        prompt = details.get('prompt') or ''
        return f"[prompt] {_truncate(prompt, 70)}"

    return f"[{operation}] {_truncate(str(details), 60)}"


def _get_bead_type(operation: str) -> str:
    """
    Determine bead type based on operation.

    Maps operations to semantic bead types for better filtering.
    """
    type_map = {
        'write': 'task',
        'edit': 'task',
        'task': 'task',
        'bash': 'task',
        'user_prompt': 'note',
    }
    return type_map.get(operation, 'task')


def _create_bead_description(
    operation: str,
    details: Dict[str, Any],
    session_id: str,
    project: str,
    cwd: str
) -> str:
    """Generate description for the bead."""
    lines = []

    # Add context
    lines.append(f"**Session:** {session_id}")
    lines.append(f"**Project:** {project}")
    lines.append(f"**Working Dir:** {cwd}")
    lines.append("")

    # Operation-specific details
    if operation == 'write':
        lines.append(f"**File:** {details.get('file_path') or 'unknown'}")
    elif operation == 'edit':
        lines.append(f"**File:** {details.get('file_path') or 'unknown'}")
        if details.get('old_string'):
            lines.append(f"**Changed:** {_truncate(details['old_string'], 50)}")
    elif operation == 'task':
        lines.append(f"**Agent:** {details.get('subagent_type') or 'unknown'}")
        lines.append(f"**Prompt:** {_truncate(details.get('prompt') or '', 200)}")
    elif operation == 'bash':
        lines.append(f"**Command:**\n```bash\n{details.get('command') or ''}\n```")
    elif operation == 'user_prompt':
        lines.append(f"**User Message:**\n{details.get('prompt') or ''}")

    return "\n".join(lines)


class BeadsWriter:
    """
    Writes significant operations to Beads global database.

    Called by hooks to automatically create beads for:
    - File creation (write)
    - File modification (edit)
    - Subagent launches (task)
    - Significant bash commands
    - Meaningful user prompts
    """

    def __init__(self):
        self.db = _get_beads_db()
        self._error_logged = False

    def _log_error(self, msg: str):
        """Log error once to avoid spam."""
        if not self._error_logged:
            print(f"[beads_writer] {msg}", file=sys.stderr)
            self._error_logged = True

    def on_tool_use(
        self,
        operation: str,
        prompt: str,
        details: Dict[str, Any],
        session_id: str,
        project: str,
        cwd: str,
        subagent_context: Optional[Dict] = None
    ):
        """
        Called by pre-tool-use.py on every tool invocation.
        Creates a bead if the operation is significant.
        """
        if not self.db:
            return

        if not _is_significant_operation(operation, details):
            return

        try:
            # Generate content hash for deduplication
            prompt_snippet = (prompt or '')[:50]
            content_key = f"{operation}:{details.get('file_path') or ''}:{details.get('command') or ''}:{prompt_snippet}"
            content_hash = _content_hash(content_key)

            # Skip if we've already created a bead for this content this session
            if not _add_to_cache(content_hash):
                return

            # Create the bead (with secrets redacted)
            title = redact_secrets(_create_bead_title(operation, details))
            description = redact_secrets(_create_bead_description(operation, details, session_id, project, cwd))
            bead_type = _get_bead_type(operation)

            # Labels for filtering
            labels = ['auto', operation]
            if subagent_context and subagent_context.get('is_subagent'):
                labels.append('subagent')

            self.db.create(
                title=title,
                type=bead_type,
                description=description,
                labels=labels,
                created_by=f"hook:{session_id}"
            )

        except Exception as e:
            self._log_error(f"Failed to create bead: {e}")

    def on_user_prompt(
        self,
        prompt: str,
        session_id: str,
        project: str,
        cwd: str
    ):
        """
        Called by user-prompt-submit.py on every user message.
        Creates a bead for meaningful user prompts.
        """
        if not self.db:
            return

        details = {'prompt': prompt}

        if not _is_significant_operation('user_prompt', details):
            return

        try:
            # Generate content hash
            prompt_text = prompt or ''
            content_hash = _content_hash(f"user_prompt:{prompt_text[:100]}")

            if not _add_to_cache(content_hash):
                return

            title = redact_secrets(_create_bead_title('user_prompt', details))
            description = redact_secrets(_create_bead_description('user_prompt', details, session_id, project, cwd))

            self.db.create(
                title=title,
                type='note',  # User prompts are notes, not tasks
                description=description,
                labels=['auto', 'user_prompt'],
                created_by=f"hook:{session_id}"
            )

        except Exception as e:
            self._log_error(f"Failed to create user_prompt bead: {e}")


# Singleton instance
_writer: Optional[BeadsWriter] = None


def get_writer() -> BeadsWriter:
    """Get or create the singleton BeadsWriter instance."""
    global _writer
    if _writer is None:
        _writer = BeadsWriter()
    return _writer


def on_tool_use(
    operation: str,
    prompt: str,
    details: Dict[str, Any],
    session_id: str,
    project: str,
    cwd: str,
    subagent_context: Optional[Dict] = None
):
    """
    Hook entry point for tool use events.
    Called from pre-tool-use.py.
    """
    get_writer().on_tool_use(
        operation=operation,
        prompt=prompt,
        details=details,
        session_id=session_id,
        project=project,
        cwd=cwd,
        subagent_context=subagent_context
    )


def on_user_prompt(
    prompt: str,
    session_id: str,
    project: str,
    cwd: str
):
    """
    Hook entry point for user prompt events.
    Called from user-prompt-submit.py.
    """
    get_writer().on_user_prompt(
        prompt=prompt,
        session_id=session_id,
        project=project,
        cwd=cwd
    )


# ── pi-coding-agent bridge entry point ──────────────────────────────────────
# When called with --from-pi, reads JSON args from stdin and dispatches
# to the appropriate hook function. Allows the TypeScript bridge to call
# this module without reimplementing any logic.
# JSON schema: { "op": "on_tool_use"|"user_prompt", ...fields }
def _run_from_pi():
    import sys as _sys
    try:
        raw = _sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except Exception:
        _sys.exit(0)

    op = args.get("op", "")
    _ensure_beads_in_path()

    if op == "user_prompt":
        on_user_prompt(
            prompt=args.get("prompt", ""),
            session_id=args.get("session_id", ""),
            project=args.get("project", ""),
            cwd=args.get("cwd", ""),
        )
    else:
        on_tool_use(
            operation=args.get("op", args.get("operation", "unknown")),
            prompt=args.get("prompt", ""),
            details=args.get("details", {}),
            session_id=args.get("session_id", ""),
            project=args.get("project", ""),
            cwd=args.get("cwd", ""),
            subagent_context=args.get("subagent_context"),
        )


# Test when run directly
if __name__ == "__main__":
    if "--from-pi" in sys.argv:
        _run_from_pi()
        sys.exit(0)
    print("Testing BeadsWriter...")

    # Test path discovery
    beads_path = _get_beads_path()
    if beads_path:
        print(f"✓ Found beads at: {beads_path}")
    else:
        print("✗ Could not find beads module")
        print("  Set BEADS_PATH environment variable to the scripts directory")
        sys.exit(1)

    writer = get_writer()
    if writer.db:
        print(f"✓ Connected to global beads: {GLOBAL_BEADS_DIR}")

        # Test creating a bead
        writer.on_tool_use(
            operation="write",
            prompt="Test bead creation",
            details={"file_path": "/tmp/test.py"},
            session_id="test-session",
            project="test-project",
            cwd="/tmp"
        )
        print("✓ Created test bead")

        # Test cache limiting
        print(f"✓ Cache size limit: {_MAX_CACHE_SIZE}")
    else:
        print("✗ Could not connect to beads database")
