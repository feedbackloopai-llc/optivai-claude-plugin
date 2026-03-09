#!/usr/bin/env python3
"""
Core logging module for Claude Code automatic activity tracking
Global installation with per-project log isolation

Supports:
- Local JSON Lines logging (per-project)
- Provider metadata capture (Teams, Bedrock, Direct)
- PostgreSQL sync (async, separate process)
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional


class AgentActivityLogger:
    """Core logger for Claude Code agent activities with per-project isolation"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the activity logger

        Args:
            config_path: Path to configuration file (defaults to ~/.claude/hooks/auto-logger-config.json)
        """
        # Global config location
        self.global_claude_dir = Path.home() / ".claude"
        self.hooks_dir = self.global_claude_dir / "hooks"

        # Load configuration
        self.config = self._load_config(config_path)

        # Per-project log directory
        self.project_dir = Path.cwd()
        self.project_name = self.project_dir.name
        self.log_dir = self._get_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Get user info
        self.user = self._get_user()

        # Agent identity (BD_ACTOR pattern from Gastown)
        self.agent_id = self._get_agent_id()

        # Provider environment info (replaces legacy bedrock detection)
        self.provider_env = self._get_provider_env()

        # Session management
        self.session_id = os.environ.get('CLAUDE_CODE_SESSION_ID', self._generate_session_id())
        os.environ['CLAUDE_CODE_SESSION_ID'] = self.session_id
        self._write_session_metadata()

    def _load_config(self, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        if config_path is None:
            config_path = self.hooks_dir / "auto-logger-config.json"

        default_config = {
            "enabled": True,
            "log_tool_operations": True,
            "log_user_prompts": True,
            "log_level": "info",
            "max_prompt_length": 500,
            "session_tracking": True,
            "log_directory_mode": "per_project",  # "per_project" or "global"
            "global_log_path": str(self.global_claude_dir / "logs"),
            "project_log_subdir": ".claude/logs",
            "destinations": {
                "local": {
                    "enabled": True
                },
                "postgresql": {
                    "enabled": False,
                    "account": "",
                    "warehouse": "",
                    "database": "",
                    "schema": "CLAUDE_LOGS",
                    "table": "CLAUDE_ACTIVITY_LOG"
                }
            }
        }

        if Path(config_path).exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    # Deep merge for destinations
                    if "destinations" in user_config:
                        for dest, dest_config in user_config["destinations"].items():
                            if dest in default_config["destinations"]:
                                default_config["destinations"][dest].update(dest_config)
                            else:
                                default_config["destinations"][dest] = dest_config
                        del user_config["destinations"]
                    default_config.update(user_config)
            except Exception as e:
                self._log_error(f"Failed to load config: {e}")

        return default_config

    def _get_log_dir(self) -> Path:
        """Determine log directory based on config"""
        mode = self.config.get("log_directory_mode", "per_project")

        if mode == "global":
            # All logs in one global directory
            return Path(self.config.get("global_log_path", str(self.global_claude_dir / "logs"))).expanduser()
        else:
            # Per-project logs (default)
            subdir = self.config.get("project_log_subdir", ".claude/logs")
            return self.project_dir / subdir

    def _get_user(self) -> str:
        """Get current user identifier"""
        # Try various environment variables
        for var in ['USER', 'USERNAME', 'LOGNAME']:
            user = os.environ.get(var)
            if user:
                return user
        return "unknown"

    def _get_agent_id(self) -> str:
        """
        Get agent identity from environment or generate default.

        Format: {project}/{role}/{name} (Gastown BD_ACTOR pattern)
        Examples: gz-plugin/crew/chris, data-factory/polecat/worker-1
        """
        # Check environment variable first
        agent_id = os.environ.get('CLAUDE_AGENT_ID')
        if agent_id:
            return agent_id

        # Check config for default
        agent_config = self.config.get("agent_identity", {})
        if agent_config.get("default_agent_id"):
            return agent_config["default_agent_id"]

        # Generate default: {project}/crew/{user}
        return f"{self.project_name}/crew/{self.user}"

    def _get_provider_env(self) -> Dict[str, Any]:
        """Detect Claude Code provider environment (Bedrock, Teams, or Direct API).

        Priority:
          1. Bedrock - CLAUDE_CODE_USE_BEDROCK=1
          2. Teams/Enterprise/Pro/Max - default for non-Bedrock
        """
        # Priority 1: Bedrock (existing AWS customers)
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
            provider = {
                "type": "bedrock",
                "model": os.environ.get("ANTHROPIC_MODEL", "unknown"),
                "aws_region": os.environ.get("AWS_REGION", ""),
            }
            if os.environ.get("AWS_PROFILE"):
                provider["aws_profile"] = os.environ["AWS_PROFILE"]
            if os.environ.get("AWS_ROLE_ARN"):
                provider["aws_role_arn"] = os.environ["AWS_ROLE_ARN"]
            return provider

        # Priority 2: Claude Teams/Enterprise/Pro/Max (the new default)
        return {
            "type": "teams",
            "model": os.environ.get("ANTHROPIC_MODEL", "unknown"),
            "organization": os.environ.get("CLAUDE_ORG_NAME", "FeedbackLoopAI"),
            "user_email": os.environ.get("CLAUDE_USER_EMAIL", ""),
        }

    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        from uuid import uuid4
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_uuid = str(uuid4())[:8]
        return f"session-{timestamp}-{short_uuid}"

    def _write_session_metadata(self):
        """Append session metadata to sessions.jsonl file"""
        if not self.config.get("session_tracking", True):
            return

        sessions_file = self.log_dir / "sessions.jsonl"

        # Check if this session already logged
        if sessions_file.exists():
            try:
                with open(sessions_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if self.session_id in line:
                            return  # Already logged
            except:
                pass

        metadata = {
            "session_id": self.session_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "user": self.user,
            "cwd": str(self.project_dir),
            "project": self.project_name,
            "provider": self.provider_env
        }

        try:
            with open(sessions_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(metadata) + "\n")
        except Exception as e:
            self._log_error(f"Failed to write session metadata: {e}")

    def _get_log_file(self) -> Path:
        """Get the current log file path (daily rotation)"""
        date = datetime.now().strftime("%Y-%m-%d")
        filename = f"agent-activity-{date}.log"
        return self.log_dir / filename

    def _log_error(self, error_msg: str):
        """Internal error logging"""
        try:
            error_file = self.log_dir / "logger_errors.log"
            with open(error_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.now(timezone.utc).isoformat()
                f.write(f"{timestamp} ERROR: {error_msg}\n")
        except:
            pass

    def log_activity(self, activity_data: Dict[str, Any]):
        """
        Log activity data in JSON format

        Args:
            activity_data: Dictionary containing activity information
        """
        if not self.config.get("enabled", True):
            return

        now = datetime.now(timezone.utc)
        now_local = datetime.now()
        log_file = self._get_log_file()

        # Truncate long prompts
        max_length = self.config.get("max_prompt_length", 500)
        prompt = activity_data.get("prompt", "")
        if len(prompt) > max_length:
            prompt = prompt[:max_length] + "..."

        # Create log entry with all metadata
        log_entry = {
            # Precomputed time fields for efficient querying
            "epoch": int(time.time()),
            "date": now_local.strftime('%Y-%m-%d'),
            "year": now_local.year,
            "month": now_local.month,
            "day": now_local.day,
            "hour": now_local.hour,
            # Timestamps
            "timestamp": now.isoformat(),
            "time": activity_data.get("timestamp", now_local.strftime('%H:%M:%S')),
            # Core fields
            "operation": activity_data.get("operation", "unknown"),
            "prompt": prompt,
            "session_id": activity_data.get("session_id", self.session_id),
            "agent_id": self.agent_id,  # Gastown BD_ACTOR pattern
            # Context
            "user": self.user,
            "cwd": str(self.project_dir),
            "project": self.project_name,
        }

        # Add provider metadata
        log_entry["provider"] = self.provider_env

        # Add details object with tool-specific metadata
        if "details" in activity_data and activity_data["details"]:
            details = activity_data["details"]
            log_entry["details"] = details

            # Extract subagent context fields to top-level for easier querying
            # This makes it simpler to filter/aggregate by subagent in downstream analysis
            if details.get("executing_subagent"):
                log_entry["subagent"] = {
                    "type": details.get("executing_subagent"),
                    "id": details.get("executing_subagent_id"),
                    "depth": details.get("subagent_depth", 0),
                    "lineage": details.get("subagent_lineage", [])
                }
            elif details.get("subagent_type") and details.get("subagent_id"):
                # This is a Task operation (spawning a subagent)
                log_entry["subagent"] = {
                    "type": details.get("subagent_type"),
                    "id": details.get("subagent_id"),
                    "depth": details.get("depth", 1),
                    "lineage": details.get("lineage", []),
                    "parent_id": details.get("parent_subagent_id"),
                    "is_spawn": True
                }

        # Legacy fields for backwards compatibility
        if "result" in activity_data and activity_data["result"]:
            log_entry["result"] = str(activity_data["result"])[:200]
        if "context" in activity_data:
            log_entry["context"] = activity_data["context"]

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            self._log_error(f"Failed to log activity: {e}")

    def log_user_prompt(self, user_prompt: str, details: Optional[Dict] = None):
        """Log a user prompt"""
        self.log_activity({
            "operation": "user_prompt",
            "prompt": user_prompt,
            "session_id": self.session_id,
            "details": details or {}
        })

    def get_session_log_path(self) -> Path:
        """Get the path to the current session's log file"""
        return self._get_log_file()

    def get_recent_activities(self, limit: int = 20) -> list:
        """Read recent activities from log file for context priming"""
        log_file = self._get_log_file()

        if not log_file.exists():
            return []

        activities = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

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


# Singleton instance
_logger_instance = None


def get_logger() -> AgentActivityLogger:
    """Get a singleton logger instance"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = AgentActivityLogger()
    return _logger_instance


def log_activity(activity_data: Dict[str, Any]):
    """Convenience function to log activity data"""
    get_logger().log_activity(activity_data)


def log_user_prompt(user_prompt: str, details: Optional[Dict] = None):
    """Convenience function to log user prompts"""
    get_logger().log_user_prompt(user_prompt, details)


if __name__ == "__main__":
    # Test the logger
    logger = AgentActivityLogger()
    print(f"Log directory: {logger.log_dir}")
    print(f"Log file: {logger.get_session_log_path()}")
    print(f"Session ID: {logger.session_id}")
    print(f"Provider env: {logger.provider_env}")

    logger.log_activity({
        "operation": "test",
        "prompt": "Test log entry",
        "details": {"test": True}
    })
    print("Test entry written successfully")
