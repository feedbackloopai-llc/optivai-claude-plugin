#!/usr/bin/env python3
"""
UserPromptSubmit hook for Claude Code - captures user prompts for context
Logs user inputs to provide context for subsequent tool operations

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

    def main():
        # Load configuration to check if logging is enabled
        config_path = hooks_dir.parent / "hooks" / "auto-logger-config.json"
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                if not config.get("enabled", True):
                    return  # Logging disabled
                if not config.get("log_user_prompts", True):
                    return  # User prompt logging disabled

        # Get consistent session ID (creates new one if first interaction)
        session_id = get_or_create_session_id()

        # Get project context
        cwd, project = get_project_context()

        # Get user prompt from stdin (Claude Code passes it as JSON)
        user_prompt = "Empty prompt"
        try:
            if not sys.stdin.isatty():
                stdin_data = sys.stdin.read().strip()
                if stdin_data:
                    try:
                        prompt_data = json.loads(stdin_data)
                        # Extract prompt text from various possible structures
                        if isinstance(prompt_data, dict):
                            user_prompt = prompt_data.get('prompt', prompt_data.get('text', str(prompt_data)))
                        elif isinstance(prompt_data, str):
                            user_prompt = prompt_data
                        else:
                            user_prompt = str(prompt_data)
                    except json.JSONDecodeError:
                        # If not JSON, use raw stdin
                        user_prompt = stdin_data
        except Exception as e:
            user_prompt = f"Error reading prompt: {e}"

        # Fallback to command line arguments
        if user_prompt == "Empty prompt" and len(sys.argv) > 1:
            user_prompt = " ".join(sys.argv[1:])

        # Store original length before truncation
        original_length = len(user_prompt)

        # Truncate very long prompts
        max_length = 500
        if len(user_prompt) > max_length:
            user_prompt = user_prompt[:max_length] + "..."

        timestamp = time.strftime('%H:%M:%S')

        # Log the user prompt with enhanced metadata
        logger = AgentActivityLogger()
        activity_data = {
            "operation": "user_prompt",
            "prompt": user_prompt,
            "session_id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "project": project,
            "details": {
                "prompt_length": original_length,
                "truncated": original_length > max_length,
                "interaction_type": "user_input"
            }
        }
        logger.log_activity(activity_data)

        # Store in environment for context in tool hooks
        os.environ['CLAUDE_LAST_USER_PROMPT'] = user_prompt

    if __name__ == "__main__":
        main()

except Exception as e:
    # Log the error but don't fail
    try:
        error_file = hooks_dir.parent / "logs" / "hook_errors.log"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        with open(error_file, "a", encoding='utf-8') as f:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"{timestamp} UserPromptSubmit hook error: {str(e)}\n")
    except:
        pass  # Fail silently
