#!/usr/bin/env python3
"""
PreToolUse hook for Claude Code - captures tool operations before execution
Global installation with per-project log isolation

Supports Bedrock API environment with enhanced metadata capture.
Includes subagent lineage tracking for tracing which agent performed actions.
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

# Session ID management
SESSION_ENV_VAR = 'CLAUDE_CODE_SESSION_ID'

# Subagent context tracking
SUBAGENT_CONTEXT_FILE = Path.home() / ".claude" / "logs" / "subagent_context.json"
SUBAGENT_CONTEXT_TTL_MINUTES = 30  # Context expires after 30 minutes of inactivity


def get_subagent_context():
    """Read current subagent context stack from file"""
    try:
        if SUBAGENT_CONTEXT_FILE.exists():
            with open(SUBAGENT_CONTEXT_FILE, 'r') as f:
                context = json.load(f)

            # Check if context is stale (no activity for TTL period)
            last_updated = context.get('last_updated', '')
            if last_updated:
                last_time = datetime.fromisoformat(last_updated)
                if datetime.now() - last_time > timedelta(minutes=SUBAGENT_CONTEXT_TTL_MINUTES):
                    # Context is stale, clear it
                    clear_subagent_context()
                    return None

            return context
    except (json.JSONDecodeError, Exception):
        pass
    return None


def push_subagent_context(subagent_id: str, subagent_type: str, description: str, parent_id: str = None):
    """Push a new subagent onto the context stack"""
    try:
        SUBAGENT_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)

        context = get_subagent_context() or {'stack': [], 'last_updated': ''}

        # Add new subagent to stack
        context['stack'].append({
            'subagent_id': subagent_id,
            'subagent_type': subagent_type,
            'description': description,
            'parent_id': parent_id,
            'started_at': datetime.now().isoformat()
        })
        context['last_updated'] = datetime.now().isoformat()
        context['current_subagent_id'] = subagent_id
        context['current_subagent_type'] = subagent_type

        with open(SUBAGENT_CONTEXT_FILE, 'w') as f:
            json.dump(context, f, indent=2)

    except Exception:
        pass


def update_subagent_activity():
    """Update the last_updated timestamp to keep context alive"""
    try:
        context = get_subagent_context()
        if context:
            context['last_updated'] = datetime.now().isoformat()
            with open(SUBAGENT_CONTEXT_FILE, 'w') as f:
                json.dump(context, f, indent=2)
    except Exception:
        pass


def clear_subagent_context():
    """Clear the subagent context (call when session ends or resets)"""
    try:
        if SUBAGENT_CONTEXT_FILE.exists():
            SUBAGENT_CONTEXT_FILE.unlink()
    except Exception:
        pass


def get_current_subagent_info():
    """Get info about the current subagent context for logging"""
    context = get_subagent_context()
    if not context or not context.get('stack'):
        return None

    # Return current (deepest) subagent info
    current = context['stack'][-1] if context['stack'] else None
    if current:
        return {
            'subagent_id': current.get('subagent_id'),
            'subagent_type': current.get('subagent_type'),
            'depth': len(context['stack']),
            'lineage': [s.get('subagent_type') for s in context['stack']]
        }
    return None


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


def capture_tool_info():
    """Capture tool information from Claude Code hook input"""
    tool_info = {}

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
        details['subagent_type'] = subagent_type
        if task_description:
            details['task_description'] = task_description
        task_prompt = tool_input.get('prompt', '')
        if task_prompt:
            details['task_prompt'] = task_prompt[:200] + "..." if len(task_prompt) > 200 else task_prompt

        # Generate and track subagent ID for lineage
        from uuid import uuid4
        subagent_id = f"subagent-{subagent_type}-{str(uuid4())[:8]}"
        details['subagent_id'] = subagent_id

        # Get current context to determine parent
        current_context = get_current_subagent_info()
        parent_id = current_context['subagent_id'] if current_context else None

        # Push this subagent onto the context stack
        push_subagent_context(
            subagent_id=subagent_id,
            subagent_type=subagent_type,
            description=task_description,
            parent_id=parent_id
        )

        if parent_id:
            details['parent_subagent_id'] = parent_id
            details['lineage'] = current_context.get('lineage', []) + [subagent_type]

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

        session_id = get_or_create_session_id()

        tool_info = capture_tool_info()
        operation, prompt, details = determine_operation(tool_info)

        os.environ['CLAUDE_HOOK_START_TIME'] = str(time.time())
        os.environ['CLAUDE_HOOK_OPERATION'] = operation

        # Get current subagent context for non-Task operations
        subagent_info = None
        if operation != "task":  # Task operations handle their own context
            subagent_info = get_current_subagent_info()
            if subagent_info:
                details['executing_subagent'] = subagent_info['subagent_type']
                details['executing_subagent_id'] = subagent_info['subagent_id']
                details['subagent_depth'] = subagent_info['depth']
                details['subagent_lineage'] = subagent_info['lineage']
                # Keep context alive
                update_subagent_activity()

        logger = AgentActivityLogger(config_path=config_path)
        logger.log_activity({
            "operation": operation,
            "prompt": prompt,
            "session_id": session_id,
            "timestamp": time.strftime('%H:%M:%S'),
            "details": details
        })

    except Exception as e:
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
