#!/usr/bin/env python3
"""
UserPromptSubmit hook for Claude Code - captures user prompts for context
Global installation with per-project log isolation

Supports Bedrock API environment with enhanced metadata capture.
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
                if not config.get("log_user_prompts", True):
                    return

        session_id = get_or_create_session_id()

        # Get user prompt from stdin
        user_prompt = "Empty prompt"
        try:
            if not sys.stdin.isatty():
                stdin_data = sys.stdin.read().strip()
                if stdin_data:
                    try:
                        prompt_data = json.loads(stdin_data)
                        if isinstance(prompt_data, dict):
                            user_prompt = prompt_data.get('prompt', prompt_data.get('text', str(prompt_data)))
                        elif isinstance(prompt_data, str):
                            user_prompt = prompt_data
                        else:
                            user_prompt = str(prompt_data)
                    except json.JSONDecodeError:
                        user_prompt = stdin_data
        except Exception as e:
            user_prompt = f"Error reading prompt: {e}"

        # Fallback to command line arguments
        if user_prompt == "Empty prompt" and len(sys.argv) > 1:
            user_prompt = " ".join(sys.argv[1:])

        original_length = len(user_prompt)

        # Truncate very long prompts
        max_length = 500
        if len(user_prompt) > max_length:
            user_prompt = user_prompt[:max_length] + "..."

        logger = AgentActivityLogger(config_path=config_path)
        logger.log_activity({
            "operation": "user_prompt",
            "prompt": user_prompt,
            "session_id": session_id,
            "timestamp": time.strftime('%H:%M:%S'),
            "details": {
                "prompt_length": original_length,
                "truncated": original_length > max_length,
                "interaction_type": "user_input"
            }
        })

        # Store in environment for tool hooks
        os.environ['CLAUDE_LAST_USER_PROMPT'] = user_prompt

    except Exception as e:
        try:
            error_dir = Path.cwd() / ".claude" / "logs"
            error_dir.mkdir(parents=True, exist_ok=True)
            error_file = error_dir / "hook_errors.log"
            with open(error_file, "a", encoding='utf-8') as f:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"{timestamp} UserPromptSubmit error: {str(e)}\n")
        except:
            pass


if __name__ == "__main__":
    main()
