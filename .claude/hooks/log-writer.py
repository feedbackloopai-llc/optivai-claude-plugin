#!/usr/bin/env python3
"""
Core logging module for Claude Code automatic activity tracking
Writes clean JSON logs with session tracking and daily rotation
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class AgentActivityLogger:
    """Core logger for Claude Code agent activities"""

    def __init__(self, log_dir: Optional[Path] = None, config_path: Optional[Path] = None):
        """
        Initialize the activity logger

        Args:
            log_dir: Directory for log files (defaults to .claude/logs)
            config_path: Path to configuration file (defaults to .claude/hooks/auto-logger-config.json)
        """
        # Determine base directory
        try:
            self.base_dir = Path(__file__).parent.parent
        except NameError:
            # Fallback when __file__ is not available
            self.base_dir = Path.cwd() / ".claude"

        # Load configuration
        self.config = self._load_config(config_path)

        # Setup log directory
        if log_dir is None:
            log_dir = self.base_dir / self.config.get("log_directory", "logs")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Session management
        self.session_id = os.environ.get('CLAUDE_SESSION_ID', self._generate_session_id())
        self._write_session_metadata()

    def _load_config(self, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        if config_path is None:
            config_path = self.base_dir / "hooks" / "auto-logger-config.json"

        default_config = {
            "enabled": True,
            "log_level": "info",
            "log_format": "json",
            "max_prompt_length": 500,
            "session_tracking": True,
            "log_directory": "logs"
        }

        if Path(config_path).exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception as e:
                self._log_error(f"Failed to load config: {e}")

        return default_config

    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        from uuid import uuid4
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_uuid = str(uuid4())[:8]
        return f"session-{timestamp}-{short_uuid}"

    def _write_session_metadata(self):
        """Write session metadata file"""
        if not self.config.get("session_tracking", True):
            return

        metadata_file = self.log_dir / f"{self.session_id}.metadata.json"
        metadata = {
            "session_id": self.session_id,
            "start_time": datetime.utcnow().isoformat() + "Z",
            "cwd": str(Path.cwd()),
            "log_dir": str(self.log_dir)
        }

        try:
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            self._log_error(f"Failed to write session metadata: {e}")

    def _get_log_file(self) -> Path:
        """Get the current log file path (daily rotation)"""
        date = datetime.now().strftime("%Y-%m-%d")
        filename = f"agent-activity-{date}.log"
        return self.log_dir / filename

    def _log_error(self, error_msg: str):
        """Internal error logging (fallback)"""
        try:
            error_file = self.log_dir / "logger_errors.log"
            with open(error_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.utcnow().isoformat() + "Z"
                f.write(f"{timestamp} ERROR: {error_msg}\n")
        except:
            pass  # Fail silently

    def log_operation(self, operation: str, prompt: str, result: Optional[str] = None):
        """
        Log an operation in simple text format

        Args:
            operation: Type of operation (e.g., 'file_read', 'bash_command')
            prompt: Description of what was requested
            result: Optional result or outcome
        """
        if not self.config.get("enabled", True):
            return

        timestamp = datetime.utcnow().isoformat() + "Z"
        log_file = self._get_log_file()

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp} Operation: {operation}\n")
                f.write(f"{timestamp} Prompt: {prompt}\n")
                if result:
                    f.write(f"{timestamp} Result: {result}\n")
                f.write("---\n")
        except Exception as e:
            self._log_error(f"Failed to log operation: {e}")

    def log_activity(self, activity_data: Dict[str, Any]):
        """
        Log activity data in JSON format

        Args:
            activity_data: Dictionary containing activity information
                Required keys: operation, prompt
                Optional keys: result, context, timestamp, session_id
        """
        if not self.config.get("enabled", True):
            return

        timestamp = datetime.utcnow().isoformat() + "Z"
        log_file = self._get_log_file()

        # Truncate long prompts
        max_length = self.config.get("max_prompt_length", 500)
        prompt = activity_data.get("prompt", "")
        if len(prompt) > max_length:
            prompt = prompt[:max_length] + "..."

        # Create clean log entry
        log_entry = {
            "timestamp": timestamp,
            "operation": activity_data.get("operation", "unknown"),
            "prompt": prompt,
            "session_id": activity_data.get("session_id", self.session_id),
            "time": activity_data.get("timestamp", datetime.now().strftime('%H:%M:%S'))
        }

        # Add optional fields if present
        if "result" in activity_data and activity_data["result"]:
            log_entry["result"] = str(activity_data["result"])[:200]

        if "context" in activity_data:
            log_entry["context"] = activity_data["context"]

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            self._log_error(f"Failed to log activity: {e}")

    def log_tool_use(self, tool_name: str, tool_input: Dict[str, Any], context: Optional[Dict] = None):
        """
        Convenience method for logging tool usage

        Args:
            tool_name: Name of the Claude Code tool (Read, Write, Bash, etc.)
            tool_input: Tool input parameters
            context: Additional context information
        """
        # Extract relevant info from tool_input
        prompt_parts = [tool_name]

        if "file_path" in tool_input:
            prompt_parts.append(f"file: {tool_input['file_path']}")
        elif "command" in tool_input:
            cmd = tool_input['command']
            prompt_parts.append(f"cmd: {cmd[:50]}..." if len(cmd) > 50 else f"cmd: {cmd}")
        elif "pattern" in tool_input:
            prompt_parts.append(f"pattern: {tool_input['pattern']}")

        self.log_activity({
            "operation": f"tool_{tool_name.lower()}",
            "prompt": " ".join(prompt_parts),
            "context": context or {},
            "session_id": self.session_id
        })

    def log_user_prompt(self, user_prompt: str):
        """
        Log a user prompt

        Args:
            user_prompt: The user's prompt text
        """
        self.log_activity({
            "operation": "user_prompt",
            "prompt": user_prompt,
            "session_id": self.session_id
        })

    def get_session_log_path(self) -> Path:
        """Get the path to the current session's log file"""
        return self._get_log_file()

    def get_recent_activities(self, limit: int = 20) -> list:
        """
        Read recent activities from log file

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of activity dictionaries
        """
        log_file = self._get_log_file()

        if not log_file.exists():
            return []

        activities = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Read last 'limit' lines (assuming JSON format)
            for line in reversed(lines[-limit:]):
                line = line.strip()
                if line and line.startswith('{'):
                    try:
                        activities.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            self._log_error(f"Failed to read activities: {e}")

        return list(reversed(activities))


# Convenience functions for easy import
def get_logger() -> AgentActivityLogger:
    """Get a singleton logger instance"""
    global _logger_instance
    if '_logger_instance' not in globals():
        _logger_instance = AgentActivityLogger()
    return _logger_instance


def log_operation(operation: str, prompt: str, result: Optional[str] = None):
    """Convenience function to log an operation"""
    get_logger().log_operation(operation, prompt, result)


def log_activity(activity_data: Dict[str, Any]):
    """Convenience function to log activity data"""
    get_logger().log_activity(activity_data)


def log_tool_use(tool_name: str, tool_input: Dict[str, Any], context: Optional[Dict] = None):
    """Convenience function to log tool usage"""
    get_logger().log_tool_use(tool_name, tool_input, context)


def log_user_prompt(user_prompt: str):
    """Convenience function to log user prompts"""
    get_logger().log_user_prompt(user_prompt)


# Usage example
if __name__ == "__main__":
    logger = AgentActivityLogger()
    logger.log_operation(
        operation="create_file",
        prompt="Create a new data processing module",
        result="Created src/data_processor.py with basic structure"
    )

    logger.log_activity({
        "operation": "file_read",
        "prompt": "Reading configuration file",
        "context": {"file": "config.json"}
    })
