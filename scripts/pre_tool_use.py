#!/usr/bin/env python3
"""
PreToolUse hook for Claude Code - captures tool operations before execution
Global installation with per-project log isolation

Supports Bedrock API environment with enhanced metadata capture.
Includes subagent lineage tracking for nested Task operations.
Includes memory system integration for session persistence.
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

# Session ID management
SESSION_ENV_VAR = 'CLAUDE_CODE_SESSION_ID'
SUBAGENT_ID_ENV_VAR = 'CLAUDE_SUBAGENT_ID'
SUBAGENT_TYPE_ENV_VAR = 'CLAUDE_SUBAGENT_TYPE'


def get_or_create_session_id():
    """Get existing session ID or create a new one"""
    session_id = os.environ.get(SESSION_ENV_VAR)
    if not session_id:
        from uuid import uuid4
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_uuid = str(uuid4())[:8]
        session_id = f"session-{timestamp}-{short_uuid}"
        os.environ[SESSION_ENV_VAR] = session_id
    return session_id


def get_subagent_context():
    """
    Get the current subagent execution context for logging.

    Returns dict with:
    - is_subagent: bool
    - executing_subagent: str (type)
    - executing_subagent_id: str
    - subagent_depth: int
    - subagent_lineage: list[str]
    """
    try:
        from subagent_context import get_current_context
        return get_current_context()
    except ImportError:
        return {"is_subagent": False, "subagent_depth": 0, "subagent_lineage": []}
    except Exception:
        return {"is_subagent": False, "subagent_depth": 0, "subagent_lineage": []}


def handle_task_operation(tool_input: dict, session_id: str) -> dict:
    """
    Handle Task tool operation - push subagent onto stack and return context info.

    Args:
        tool_input: The tool input containing subagent_type, description, prompt
        session_id: Current session ID

    Returns:
        Dict with subagent context for logging
    """
    try:
        from subagent_context import push_subagent, generate_subagent_id, get_current_context

        subagent_type = tool_input.get('subagent_type', 'unknown')
        task_description = tool_input.get('description', '')

        # Generate a unique ID for this subagent instance
        subagent_id = generate_subagent_id(subagent_type)

        # Get current context BEFORE pushing (to capture parent info)
        current = get_current_context()
        parent_subagent_id = current.get('executing_subagent_id') if current.get('is_subagent') else None

        # Push the new subagent onto the stack
        result = push_subagent(
            subagent_type=subagent_type,
            subagent_id=subagent_id,
            task_description=task_description,
            session_id=session_id
        )

        # Store in environment for child process access
        os.environ[SUBAGENT_ID_ENV_VAR] = subagent_id
        os.environ[SUBAGENT_TYPE_ENV_VAR] = subagent_type

        return {
            "subagent_type": subagent_type,
            "subagent_id": subagent_id,
            "task_description": task_description,
            "parent_subagent_id": parent_subagent_id,
            "lineage": result.get("lineage", [subagent_type]),
            "depth": result.get("depth", 1)
        }
    except ImportError:
        # Subagent context module not available
        return {
            "subagent_type": tool_input.get('subagent_type', 'unknown'),
            "task_description": tool_input.get('description', ''),
            "lineage_tracking": "unavailable"
        }
    except Exception as e:
        return {
            "subagent_type": tool_input.get('subagent_type', 'unknown'),
            "task_description": tool_input.get('description', ''),
            "lineage_error": str(e)
        }


def capture_tool_info():
    """Capture tool information from Claude Code hook input"""
    tool_info = {}

    # Claude Code passes tool info as JSON via stdin
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                try:
                    hook_data = json.loads(stdin_data)
                    if 'tool_name' in hook_data:
                        tool_info['tool_name'] = hook_data['tool_name']
                    if 'tool_input' in hook_data:
                        tool_info['tool_input'] = hook_data['tool_input']
                    tool_info['hook_data'] = hook_data
                except json.JSONDecodeError as e:
                    tool_info['raw_stdin'] = stdin_data
                    tool_info['parse_error'] = str(e)
    except Exception as e:
        tool_info['stdin_error'] = str(e)

    return tool_info


def determine_operation(tool_info):
    """Determine the operation type and create meaningful log entry with rich metadata"""
    operation = "unknown"
    prompt = "Unknown operation"
    details = {}

    tool_input = tool_info.get('tool_input', {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    file_path = tool_input.get('file_path')
    tool_name = tool_info.get('tool_name', '')

    if tool_name:
        details['tool_name'] = tool_name

    if tool_name == "Read":
        operation = "read"
        prompt = f"read: {file_path}" if file_path else "read operation"
        if file_path:
            details['file_path'] = file_path
            details['file_name'] = Path(file_path).name
        if 'offset' in tool_input:
            details['offset'] = tool_input['offset']
        if 'limit' in tool_input:
            details['limit'] = tool_input['limit']

    elif tool_name == "Write":
        operation = "write"
        prompt = f"write: {file_path}" if file_path else "write operation"
        if file_path:
            details['file_path'] = file_path
            details['file_name'] = Path(file_path).name
        content = tool_input.get('content', '')
        details['content_length'] = len(content) if content else 0

    elif tool_name in ["Edit", "MultiEdit"]:
        operation = "edit"
        prompt = f"edit: {file_path}" if file_path else "edit operation"
        if file_path:
            details['file_path'] = file_path
            details['file_name'] = Path(file_path).name
        if 'old_string' in tool_input:
            details['old_string_length'] = len(tool_input['old_string'])
        if 'new_string' in tool_input:
            details['new_string_length'] = len(tool_input['new_string'])

    elif tool_name == "Bash":
        operation = "bash"
        command = tool_input.get('command', 'unknown command')
        description = tool_input.get('description', '')
        if description:
            prompt = description
            details['description'] = description
        else:
            prompt = f"bash: {command[:100]}..." if len(command) > 100 else f"bash: {command}"
        details['command'] = command[:500] if len(command) > 500 else command
        if tool_input.get('timeout'):
            details['timeout'] = tool_input['timeout']

    elif tool_name == "Glob":
        operation = "glob"
        pattern = tool_input.get('pattern', '')
        search_path = tool_input.get('path', '.')
        prompt = f"glob: {pattern}" if pattern else "glob search"
        details['pattern'] = pattern
        details['search_path'] = search_path

    elif tool_name == "Grep":
        operation = "grep"
        pattern = tool_input.get('pattern', '')
        search_path = tool_input.get('path', '.')
        output_mode = tool_input.get('output_mode', 'files_with_matches')
        prompt = f"grep: {pattern}" if pattern else "grep search"
        details['pattern'] = pattern
        details['search_path'] = search_path
        details['output_mode'] = output_mode
        if tool_input.get('glob'):
            details['glob_filter'] = tool_input['glob']

    elif tool_name == "Task":
        operation = "task"
        subagent_type = tool_input.get('subagent_type', '')
        task_description = tool_input.get('description', '')
        prompt = f"task: {subagent_type}" if subagent_type else "task operation"
        # Note: Task details are populated separately by handle_task_operation()
        # We mark this as a task operation and let main() handle the subagent context
        details['subagent_type'] = subagent_type
        if task_description:
            details['task_description'] = task_description
        task_prompt = tool_input.get('prompt', '')
        if task_prompt:
            details['task_prompt'] = task_prompt[:200] + "..." if len(task_prompt) > 200 else task_prompt
        details['_is_task_operation'] = True  # Flag for special handling

    elif tool_name == "WebFetch":
        operation = "web_fetch"
        url = tool_input.get('url', '')
        prompt = f"web_fetch: {url}" if url else "web fetch operation"
        details['url'] = url
        if tool_input.get('prompt'):
            details['fetch_prompt'] = tool_input['prompt'][:100]

    elif tool_name == "WebSearch":
        operation = "web_search"
        query = tool_input.get('query', '')
        prompt = f"web_search: {query}" if query else "web search operation"
        details['query'] = query

    elif tool_name == "TodoWrite":
        operation = "todo_write"
        todos = tool_input.get('todos', [])
        prompt = f"todo_write: {len(todos)} items"
        details['todo_count'] = len(todos)
        if todos:
            statuses = {}
            for todo in todos:
                status = todo.get('status', 'unknown')
                statuses[status] = statuses.get(status, 0) + 1
            details['status_summary'] = statuses

    elif tool_name == "SlashCommand":
        operation = "slash_command"
        command = tool_input.get('command', '')
        prompt = f"slash_command: {command}" if command else "slash command"
        details['command'] = command

    elif tool_name == "AskUserQuestion":
        operation = "ask_user"
        questions = tool_input.get('questions', [])
        prompt = f"ask_user: {len(questions)} question(s)"
        details['question_count'] = len(questions)
        if questions:
            details['first_question'] = questions[0].get('question', '')[:100]

    elif tool_name:
        operation = tool_name.lower()
        prompt = f"{tool_name}: {file_path}" if file_path else f"{tool_name} operation"
        if file_path:
            details['file_path'] = file_path

    elif file_path:
        operation = "file_operation"
        prompt = f"file_operation: {file_path}"
        details['file_path'] = file_path

    return operation, prompt, details


def get_config_path():
    """Get config path - check project first, then global"""
    # Project-level config
    project_config = Path.cwd() / ".claude" / "hooks" / "auto-logger-config.json"
    if project_config.exists():
        return project_config

    # Global config
    global_config = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    return global_config


def main():
    try:
        from log_writer import AgentActivityLogger

        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                if not config.get("enabled", True):
                    return
                if not config.get("log_tool_operations", True):
                    return

        # Get session ID
        session_id = get_or_create_session_id()

        # Capture tool information
        tool_info = capture_tool_info()
        tool_input = tool_info.get('tool_input', {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        operation, prompt, details = determine_operation(tool_info)

        # Store for potential post-hook use
        os.environ['CLAUDE_HOOK_START_TIME'] = str(time.time())
        os.environ['CLAUDE_HOOK_OPERATION'] = operation

        # Handle subagent context based on operation type
        subagent_ctx = None
        if details.get('_is_task_operation'):
            # This is a Task operation - push subagent onto stack
            del details['_is_task_operation']  # Remove internal flag
            task_context = handle_task_operation(tool_input, session_id)
            # Merge task context into details
            details.update(task_context)
        else:
            # For all other operations, include current subagent context
            subagent_ctx = get_subagent_context()
            if subagent_ctx.get('is_subagent'):
                # We're executing within a subagent - add lineage info
                details['executing_subagent'] = subagent_ctx.get('executing_subagent')
                details['executing_subagent_id'] = subagent_ctx.get('executing_subagent_id')
                details['subagent_depth'] = subagent_ctx.get('subagent_depth', 0)
                details['subagent_lineage'] = subagent_ctx.get('subagent_lineage', [])

        # Log the operation
        logger = AgentActivityLogger(config_path=config_path)
        logger.log_activity({
            "operation": operation,
            "prompt": prompt,
            "session_id": session_id,
            "timestamp": time.strftime('%H:%M:%S'),
            "details": details
        })

        # Update memory system (session state, work log, recovery checkpoint)
        try:
            from memory_writer import on_tool_use, on_todo_write

            # Get subagent context for memory if not already fetched
            if subagent_ctx is None:
                subagent_ctx = get_subagent_context()

            # Update memory for all operations
            on_tool_use(
                operation=operation,
                prompt=prompt,
                details=details,
                session_id=session_id,
                project=logger.project_name,
                cwd=str(logger.project_dir),
                subagent_context=subagent_ctx if subagent_ctx.get('is_subagent') else None
            )

            # Sync TodoWrite operations to planned_tasks.yaml
            if operation == 'todo_write':
                todos = tool_input.get('todos', [])
                if todos:
                    on_todo_write(todos)

        except ImportError:
            pass  # Memory module not available
        except Exception as mem_err:
            # Log memory errors but don't fail the hook
            try:
                error_dir = Path.cwd() / ".claude" / "logs"
                error_dir.mkdir(parents=True, exist_ok=True)
                error_file = error_dir / "hook_errors.log"
                with open(error_file, "a", encoding='utf-8') as f:
                    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                    f.write(f"{timestamp} Memory update error: {str(mem_err)}\n")
            except:
                pass

    except Exception as e:
        # Log errors for debugging
        try:
            error_dir = Path.cwd() / ".claude" / "logs"
            error_dir.mkdir(parents=True, exist_ok=True)
            error_file = error_dir / "hook_errors.log"
            with open(error_file, "a", encoding='utf-8') as f:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"{timestamp} PreToolUse error: {str(e)}\n")
        except:
            pass


if __name__ == "__main__":
    main()
