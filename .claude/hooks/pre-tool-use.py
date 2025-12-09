#!/usr/bin/env python3
"""
PreToolUse hook for Claude Code - captures tool operations before execution
Logs all tool usage with context for activity tracking
"""

import sys
import os
import time
import json
from pathlib import Path

# Add hooks directory to path
hooks_dir = Path(__file__).parent
sys.path.insert(0, str(hooks_dir))

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
        """Determine the operation type and create meaningful log entry"""
        operation = "unknown"
        prompt = "Unknown operation"

        # Extract file path from tool_input if available
        file_path = None
        if 'tool_input' in tool_info and isinstance(tool_info['tool_input'], dict):
            file_path = tool_info['tool_input'].get('file_path')

        if 'tool_name' in tool_info:
            tool_name = tool_info['tool_name']

            # Map Claude Code tool names to operations
            if tool_name == "Read":
                operation = "read"
                prompt = f"read: {file_path}" if file_path else "read operation"
            elif tool_name == "Write":
                operation = "write"
                prompt = f"write: {file_path}" if file_path else "write operation"
            elif tool_name in ["Edit", "MultiEdit"]:
                operation = "edit"
                prompt = f"edit: {file_path}" if file_path else "edit operation"
            elif tool_name == "Bash":
                operation = "bash"
                if 'tool_input' in tool_info and isinstance(tool_info['tool_input'], dict):
                    command = tool_info['tool_input'].get('command', 'unknown command')
                    prompt = f"bash: {command[:50]}..." if len(command) > 50 else f"bash: {command}"
                else:
                    prompt = "bash command"
            elif tool_name == "Glob":
                operation = "glob"
                pattern = tool_info['tool_input'].get('pattern', '') if 'tool_input' in tool_info else ''
                prompt = f"glob: {pattern}" if pattern else "glob search"
            elif tool_name == "Grep":
                operation = "grep"
                pattern = tool_info['tool_input'].get('pattern', '') if 'tool_input' in tool_info else ''
                prompt = f"grep: {pattern}" if pattern else "grep search"
            elif tool_name == "Task":
                operation = "task"
                subagent_type = tool_info['tool_input'].get('subagent_type', '') if 'tool_input' in tool_info else ''
                prompt = f"task: {subagent_type}" if subagent_type else "task operation"
            elif tool_name == "WebFetch":
                operation = "web_fetch"
                url = tool_info['tool_input'].get('url', '') if 'tool_input' in tool_info else ''
                prompt = f"web_fetch: {url}" if url else "web fetch operation"
            elif tool_name == "WebSearch":
                operation = "web_search"
                query = tool_info['tool_input'].get('query', '') if 'tool_input' in tool_info else ''
                prompt = f"web_search: {query}" if query else "web search operation"
            elif tool_name == "TodoWrite":
                operation = "todo_write"
                prompt = "todo_write: updating task list"
            elif tool_name == "SlashCommand":
                operation = "slash_command"
                command = tool_info['tool_input'].get('command', '') if 'tool_input' in tool_info else ''
                prompt = f"slash_command: {command}" if command else "slash command"
            else:
                operation = tool_name.lower()
                prompt = f"{tool_name}: {file_path}" if file_path else f"{tool_name} operation"

        # Fallback to file path detection
        elif file_path or 'file_path' in tool_info:
            file_path = file_path or tool_info.get('file_path')
            operation = "file_operation"
            prompt = f"file_operation: {file_path}"

        return operation, prompt

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

        # Capture tool information
        tool_info = capture_tool_info()
        operation, prompt = determine_operation(tool_info)

        # Store for post-hook (if needed in future)
        os.environ['CLAUDE_HOOK_START_TIME'] = str(time.time())
        os.environ['CLAUDE_HOOK_OPERATION'] = operation
        os.environ['CLAUDE_HOOK_PROMPT'] = prompt
        os.environ['CLAUDE_HOOK_TOOL_INFO'] = json.dumps(tool_info)

        # Log the operation
        logger = AgentActivityLogger()
        activity_data = {
            "operation": operation,
            "prompt": prompt,
            "session_id": logger.session_id,
            "timestamp": time.strftime('%H:%M:%S')
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
