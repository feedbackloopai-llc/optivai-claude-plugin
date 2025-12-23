#!/usr/bin/env python3
"""
Core logging module for Claude Code automatic activity tracking
Global installation with per-project log isolation

Supports:
- Local JSON Lines logging (per-project)
- Bedrock API metadata capture
- Persistent Memory System (session state, tasks, work log, recovery)
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Optional YAML support - graceful degradation if not installed
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


class MemorySystemManager:
    """
    Manages the persistent memory system for Claude Code agents.

    Memory files (at ~/.claude/claude-code-memory/):
    - session_state.yaml: Current session context and focus
    - planned_tasks.yaml: Tasks to complete (remove when done)
    - work_log.yaml: Chronological action history
    - recovery_checkpoint.yaml: Crash recovery context
    """

    MEMORY_FILES = {
        "session_state.yaml": "Current session context and focus",
        "planned_tasks.yaml": "Tasks to complete (remove when done)",
        "work_log.yaml": "Chronological action history",
        "recovery_checkpoint.yaml": "Crash recovery context"
    }

    def __init__(self, memory_dir: Optional[Path] = None):
        """Initialize the memory system manager"""
        self.memory_dir = memory_dir or (Path.home() / ".claude" / "claude-code-memory")
        self._ensure_memory_dir()

    def _ensure_memory_dir(self):
        """Ensure memory directory exists"""
        if not self.memory_dir.exists():
            self.memory_dir.mkdir(parents=True, exist_ok=True)

    def _read_yaml(self, filepath: Path) -> Optional[Dict]:
        """Read YAML file, return None if not found or error"""
        if not filepath.exists():
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                if YAML_AVAILABLE:
                    return yaml.safe_load(content)
                else:
                    # Basic parsing if YAML not available
                    return {"raw_content": content, "yaml_unavailable": True}
        except Exception as e:
            return {"error": str(e)}

    def _write_yaml(self, filepath: Path, data: Dict) -> bool:
        """Write data to YAML file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                if YAML_AVAILABLE:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                else:
                    # Fallback to JSON-like format
                    f.write("# YAML library not available - using JSON format\n")
                    json.dump(data, f, indent=2)
            return True
        except Exception as e:
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get status of all memory files"""
        status = {
            "memory_dir": str(self.memory_dir),
            "exists": self.memory_dir.exists(),
            "yaml_available": YAML_AVAILABLE,
            "files": {}
        }

        for filename, description in self.MEMORY_FILES.items():
            filepath = self.memory_dir / filename
            file_status = {
                "description": description,
                "exists": filepath.exists(),
                "path": str(filepath)
            }

            if filepath.exists():
                stat = filepath.stat()
                file_status["size_bytes"] = stat.st_size
                file_status["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

            status["files"][filename] = file_status

        return status

    def read_file(self, filename: str) -> Optional[Dict]:
        """Read a specific memory file"""
        if filename not in self.MEMORY_FILES:
            return {"error": f"Unknown memory file: {filename}"}

        filepath = self.memory_dir / filename
        return self._read_yaml(filepath)

    def read_all(self) -> Dict[str, Any]:
        """Read all memory files"""
        result = {}
        for filename in self.MEMORY_FILES:
            result[filename] = self.read_file(filename)
        return result

    def get_session_state(self) -> Optional[Dict]:
        """Get current session state"""
        return self.read_file("session_state.yaml")

    def get_planned_tasks(self) -> Optional[Dict]:
        """Get planned tasks"""
        return self.read_file("planned_tasks.yaml")

    def get_work_log(self) -> Optional[Dict]:
        """Get work log"""
        return self.read_file("work_log.yaml")

    def get_recovery_checkpoint(self) -> Optional[Dict]:
        """Get recovery checkpoint"""
        return self.read_file("recovery_checkpoint.yaml")

    def update_session_state(self, session_data: Dict) -> bool:
        """Update session state with new data"""
        filepath = self.memory_dir / "session_state.yaml"
        existing = self._read_yaml(filepath) or {}

        # Merge with existing, updating timestamp
        existing.update(session_data)
        if "session" not in existing:
            existing["session"] = {}
        existing["session"]["last_updated"] = datetime.now(timezone.utc).isoformat()

        return self._write_yaml(filepath, existing)

    def add_planned_task(self, task: Dict) -> bool:
        """Add a task to planned tasks"""
        filepath = self.memory_dir / "planned_tasks.yaml"
        existing = self._read_yaml(filepath) or {"tasks": []}

        if "tasks" not in existing:
            existing["tasks"] = []

        task["added_at"] = datetime.now(timezone.utc).isoformat()
        existing["tasks"].append(task)

        return self._write_yaml(filepath, existing)

    def remove_planned_task(self, task_id: str) -> bool:
        """Remove a completed task from planned tasks"""
        filepath = self.memory_dir / "planned_tasks.yaml"
        existing = self._read_yaml(filepath)

        if not existing or "tasks" not in existing:
            return False

        existing["tasks"] = [t for t in existing["tasks"] if t.get("id") != task_id]
        return self._write_yaml(filepath, existing)

    def add_work_log_entry(self, entry: Dict) -> bool:
        """Add an entry to the work log"""
        filepath = self.memory_dir / "work_log.yaml"
        existing = self._read_yaml(filepath) or {"entries": []}

        if "entries" not in existing:
            existing["entries"] = []

        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        existing["entries"].append(entry)

        # Keep only last 100 entries to prevent file from growing too large
        if len(existing["entries"]) > 100:
            existing["entries"] = existing["entries"][-100:]

        return self._write_yaml(filepath, existing)

    def create_checkpoint(self, checkpoint_data: Dict) -> bool:
        """Create a recovery checkpoint"""
        filepath = self.memory_dir / "recovery_checkpoint.yaml"

        checkpoint_data["checkpoint"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": checkpoint_data.get("status", "in_progress")
        }

        return self._write_yaml(filepath, checkpoint_data)

    def get_recovery_context(self) -> str:
        """Get formatted recovery context for agent priming"""
        output_lines = []
        output_lines.append("=" * 60)
        output_lines.append("MEMORY SYSTEM STATUS")
        output_lines.append("=" * 60)

        if not self.memory_dir.exists():
            output_lines.append(f"\n⚠️  Memory directory not found: {self.memory_dir}")
            output_lines.append("   Consider creating it for persistent session tracking.")
            return "\n".join(output_lines)

        for filename, description in self.MEMORY_FILES.items():
            filepath = self.memory_dir / filename
            output_lines.append(f"\n--- {filename} ({description}) ---")

            if filepath.exists():
                stat = filepath.stat()
                mod_time = datetime.fromtimestamp(stat.st_mtime)
                output_lines.append(f"Last Modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
                output_lines.append(f"Size: {stat.st_size} bytes")
                output_lines.append("\nContent Preview:")
                output_lines.append("-" * 40)

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        lines = content.split('\n')[:80]
                        preview = '\n'.join(lines)
                        if len(preview) > 3000:
                            preview = preview[:3000] + "\n... [truncated]"
                        output_lines.append(preview)
                except Exception as e:
                    output_lines.append(f"Error reading file: {e}")
            else:
                output_lines.append("⚠️  File not found")

        output_lines.append("\n" + "=" * 60)
        output_lines.append("END MEMORY SYSTEM STATUS")
        output_lines.append("=" * 60)

        return "\n".join(output_lines)


# Singleton memory manager instance
_memory_manager_instance = None


def get_memory_manager() -> MemorySystemManager:
    """Get a singleton memory manager instance"""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemorySystemManager()
    return _memory_manager_instance


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

        # Bedrock environment info
        self.bedrock_env = self._get_bedrock_env()

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
            "log_directory_mode": "per_project",
            "global_log_path": str(self.global_claude_dir / "logs"),
            "project_log_subdir": ".claude/logs",
            "destinations": {
                "local": {
                    "enabled": True
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
            return Path(self.config.get("global_log_path", str(self.global_claude_dir / "logs")))
        else:
            subdir = self.config.get("project_log_subdir", ".claude/logs")
            return self.project_dir / subdir

    def _get_user(self) -> str:
        """Get current user identifier"""
        for var in ['USER', 'USERNAME', 'LOGNAME']:
            user = os.environ.get(var)
            if user:
                return user
        return "unknown"

    def _get_bedrock_env(self) -> Dict[str, str]:
        """Capture Bedrock-specific environment variables"""
        bedrock_vars = {}

        if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
            bedrock_vars["is_bedrock"] = True
            bedrock_vars["aws_region"] = os.environ.get("AWS_REGION", "")
            bedrock_vars["model"] = os.environ.get("ANTHROPIC_MODEL", "")
            bedrock_vars["haiku_model"] = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "")

            if os.environ.get("AWS_PROFILE"):
                bedrock_vars["aws_profile"] = os.environ["AWS_PROFILE"]
            if os.environ.get("AWS_ROLE_ARN"):
                bedrock_vars["aws_role_arn"] = os.environ["AWS_ROLE_ARN"]
        else:
            bedrock_vars["is_bedrock"] = False

        return bedrock_vars

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

        if sessions_file.exists():
            try:
                with open(sessions_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if self.session_id in line:
                            return
            except:
                pass

        metadata = {
            "session_id": self.session_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "user": self.user,
            "cwd": str(self.project_dir),
            "project": self.project_name,
            "bedrock": self.bedrock_env
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
        """Log activity data in JSON format"""
        if not self.config.get("enabled", True):
            return

        now = datetime.now(timezone.utc)
        now_local = datetime.now()
        log_file = self._get_log_file()

        max_length = self.config.get("max_prompt_length", 500)
        prompt = activity_data.get("prompt", "")
        if len(prompt) > max_length:
            prompt = prompt[:max_length] + "..."

        log_entry = {
            "epoch": int(time.time()),
            "date": now_local.strftime('%Y-%m-%d'),
            "year": now_local.year,
            "month": now_local.month,
            "day": now_local.day,
            "hour": now_local.hour,
            "timestamp": now.isoformat(),
            "time": activity_data.get("timestamp", now_local.strftime('%H:%M:%S')),
            "operation": activity_data.get("operation", "unknown"),
            "prompt": prompt,
            "session_id": activity_data.get("session_id", self.session_id),
            "user": self.user,
            "cwd": str(self.project_dir),
            "project": self.project_name,
        }

        if self.bedrock_env.get("is_bedrock"):
            log_entry["bedrock"] = self.bedrock_env

        if "details" in activity_data and activity_data["details"]:
            log_entry["details"] = activity_data["details"]

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
    logger = AgentActivityLogger()
    print(f"Log directory: {logger.log_dir}")
    print(f"Log file: {logger.get_session_log_path()}")
    print(f"Session ID: {logger.session_id}")
    print(f"Bedrock env: {logger.bedrock_env}")

    logger.log_activity({
        "operation": "test",
        "prompt": "Test log entry",
        "details": {"test": True}
    })
    print("Test entry written successfully")
