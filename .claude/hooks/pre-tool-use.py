#!/usr/bin/env python3
"""
PreToolUse hook for Claude Code - captures tool operations before execution
Logs all tool usage with context for activity tracking

v1.3.0 - Enhanced metadata for agent context priming
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime

# Add hooks directory to path
hooks_dir = Path(__file__).parent
sys.path.insert(0, str(hooks_dir))

# Session ID management - persist across hook invocations
SESSION_ENV_VAR = 'CLAUDE_CODE_SESSION_ID'

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

def get_project_context():
    """Get current working directory and project name"""
    cwd = str(Path.cwd())
    project = Path.cwd().name
    return cwd, project

try:
    # Import log writer
    from log_writer import AgentActivityLogger

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
                        # Extract tool_name and tool_input from hook data
                        if 'tool_name' in hook_data:
                            tool_info['tool_name'] = hook_data['tool_name']
                        if 'tool_input' in hook_data:
                            tool_info['tool_input'] = hook_data['tool_input']
                        # Store the entire hook data for debugging
                        tool_info['hook_data'] = hook_data
                    except json.JSONDecodeError as e:
                        # Store raw stdin for debugging
                        tool_info['raw_stdin'] = stdin_data
                        tool_info['parse_error'] = str(e)
        except Exception as e:
            tool_info['stdin_error'] = str(e)

        # Fallback: Check environment variables that Claude Code might set
        for env_var in os.environ:
            if env_var.startswith(('TOOL_', 'CLAUDE_TOOL_', 'CC_')):
                if 'name' in env_var.lower():
                    tool_info['tool_name'] = os.environ[env_var]
                elif 'input' in env_var.lower():
                    try:
                        tool_info['tool_input'] = json.loads(os.environ[env_var])
                    except:
                        tool_info['tool_input'] = os.environ[env_var]

        return tool_info

    def determine_operation(tool_info):
        """Determine the operation type and create meaningful log entry with rich metadata"""
        operation = "unknown"
        prompt = "Unknown operation"
        details = {}  # Rich metadata for agent context

        tool_input = tool_info.get('tool_input', {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        # Extract common fields
        file_path = tool_input.get('file_path')
        tool_name = tool_info.get('tool_name', '')

        # Store tool name in details
        if tool_name:
            details['tool_name'] = tool_name

        if tool_name == "Read":
            operation = "read"
            prompt = f"read: {file_path}" if file_path else "read operation"
            if file_path:
                details['file_path'] = file_path
                details['file_name'] = Path(file_path).name if file_path else None
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
            # Use description as prompt if available, else truncated command
            if description:
                prompt = description
                details['description'] = description
            else:
                prompt = f"bash: {command[:100]}..." if len(command) > 100 else f"bash: {command}"
            # Store full command in details (up to 500 chars)
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
            # Store truncated task prompt for context
            task_prompt = tool_input.get('prompt', '')
            if task_prompt:
                details['task_prompt'] = task_prompt[:200] + "..." if len(task_prompt) > 200 else task_prompt

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
            # Summarize todo statuses
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

        # Fallback to file path detection
        elif file_path:
            operation = "file_operation"
            prompt = f"file_operation: {file_path}"
            details['file_path'] = file_path

        return operation, prompt, details

    def main():
        # Load configuration to check if logging is enabled
        config_path = hooks_dir.parent / "hooks" / "auto-logger-config.json"
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                if not config.get("enabled", True):
                    return  # Logging disabled
                if not config.get("log_tool_operations", True):
                    return  # Tool operation logging disabled

        # Get consistent session ID
        session_id = get_or_create_session_id()

        # Get project context
        cwd, project = get_project_context()

        # Capture tool information
        tool_info = capture_tool_info()
        operation, prompt, details = determine_operation(tool_info)

        # Store for post-hook (if needed in future)
        os.environ['CLAUDE_HOOK_START_TIME'] = str(time.time())
        os.environ['CLAUDE_HOOK_OPERATION'] = operation
        os.environ['CLAUDE_HOOK_PROMPT'] = prompt
        os.environ['CLAUDE_HOOK_TOOL_INFO'] = json.dumps(tool_info)

        # Log the operation with enhanced metadata
        logger = AgentActivityLogger()
        activity_data = {
            "operation": operation,
            "prompt": prompt,
            "session_id": session_id,
            "timestamp": time.strftime('%H:%M:%S'),
            "cwd": cwd,
            "project": project,
            "details": details
        }
        logger.log_activity(activity_data)

    if __name__ == "__main__":
        main()

except Exception as e:
    # Log errors for debugging
    try:
        error_file = hooks_dir.parent / "logs" / "hook_errors.log"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        with open(error_file, "a", encoding='utf-8') as f:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"{timestamp} PreToolUse error: {str(e)}\n")
    except:
        pass  # Fail silently if error logging also fails
