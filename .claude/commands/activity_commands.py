#!/usr/bin/env python3
"""
Agent Activity Commands for Claude Code
Custom /commands for logging and viewing agent activities
"""

import os
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import uuid
import re
import csv

# Import our existing logging classes
sys.path.append(str(Path(__file__).parent.parent.parent / "scripts"))
from log_writer import AgentActivityLogger, MemorySystemManager, get_memory_manager
from get_agent_history import AgentHistoryReader

class CommandResult:
    """Represents the result of a command execution"""
    def __init__(self, success: bool, message: str, data: Any = None):
        self.success = success
        self.message = message
        self.data = data

class ActivityCommands:
    """Handler class for agent activity commands"""
    
    def __init__(self):
        self.logger = AgentActivityLogger()
        self.reader = AgentHistoryReader()
        self.config_file = Path(".claude/activity-config.json")
        self.load_config()
    
    def load_config(self):
        """Load command configuration"""
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {
                "default_time_window": 24,
                "default_max_entries": 20,
                "auto_log_commands": True,
                "output_format": "rich"
            }
            self.save_config()
    
    def save_config(self):
        """Save command configuration"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

def log_activity(operation: str, prompt: str = "", result: str = "", **kwargs) -> CommandResult:
    """Log a new agent activity
    
    Usage: /log-activity "create_file" --prompt "Create data processor" --result "Created successfully"
    """
    try:
        commands = ActivityCommands()
        
        if not operation:
            return CommandResult(False, "Operation is required")
        
        commands.logger.log_operation(operation, prompt, result)
        
        message = f"✓ Logged activity: {operation}"
        if prompt:
            message += f"\n  Prompt: {prompt}"
        if result:
            message += f"\n  Result: {result}"
            
        return CommandResult(True, message)
        
    except Exception as e:
        return CommandResult(False, f"Error logging activity: {e}")

def show_history(hours: int = 24, max: int = 20, session: str = "current", 
                operation: str = None, **kwargs) -> CommandResult:
    """Show recent agent activities
    
    Usage: /show-history --hours 8 --max 10 --session current --operation create_file
    """
    try:
        commands = ActivityCommands()
        
        # Apply config defaults
        hours = hours or commands.config.get("default_time_window", 24)
        max_entries = max or commands.config.get("default_max_entries", 20)
        
        include_operations = [operation] if operation else None
        
        activities = commands.reader.read_activities(
            time_window_hours=hours,
            max_entries=max_entries,
            session_filter=session,
            include_operations=include_operations
        )
        
        if not activities:
            return CommandResult(True, "No activities found in the specified time window.")
        
        # Format output
        output = f"# Agent Activity History ({len(activities)} entries)\n"
        output += f"Time window: {hours} hours | Session: {session}\n\n"
        
        current_session = None
        for activity in activities:
            session_id = activity.get('session_id', 'unknown')
            
            if session_id != current_session:
                output += f"\n## 📁 Session: {session_id}\n"
                current_session = session_id
            
            timestamp = activity.get('timestamp', 'unknown')
            operation_name = activity.get('operation', 'unknown')
            prompt = activity.get('prompt', '')
            result = activity.get('result', '')
            
            # Format timestamp for readability
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M:%S")
            except:
                time_str = timestamp
            
            output += f"\n**{time_str}** `{operation_name}`\n"
            if prompt:
                output += f"  💬 {prompt}\n"
            if result:
                output += f"  ✅ {result}\n"
        
        return CommandResult(True, output, activities)
        
    except Exception as e:
        return CommandResult(False, f"Error retrieving history: {e}")

def session_summary(**kwargs) -> CommandResult:
    """Get summary of current session activities
    
    Usage: /session-summary
    """
    try:
        commands = ActivityCommands()
        
        activities = commands.reader.read_activities(
            time_window_hours=24,
            max_entries=1000,
            session_filter="current"
        )
        
        if not activities:
            return CommandResult(True, "No activities in current session.")
        
        # Analyze activities
        operations = {}
        start_time = None
        end_time = None
        
        for activity in activities:
            op = activity.get('operation', 'unknown')
            operations[op] = operations.get(op, 0) + 1
            
            timestamp = activity.get('timestamp')
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if start_time is None or dt < start_time:
                        start_time = dt
                    if end_time is None or dt > end_time:
                        end_time = dt
                except:
                    pass
        
        # Format summary
        session_id = commands.logger.session_id
        output = f"# 📊 Session Summary: {session_id}\n\n"
        
        if start_time and end_time:
            duration = end_time - start_time
            output += f"**Duration:** {duration}\n"
            output += f"**Started:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            output += f"**Last Activity:** {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        output += f"**Total Activities:** {len(activities)}\n\n"
        
        output += "**Operations:**\n"
        for op, count in sorted(operations.items(), key=lambda x: x[1], reverse=True):
            output += f"  • {op}: {count}\n"
        
        return CommandResult(True, output, {
            "session_id": session_id,
            "total_activities": len(activities),
            "operations": operations,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None
        })
        
    except Exception as e:
        return CommandResult(False, f"Error generating summary: {e}")

def search_logs(query: str, days: int = 7, **kwargs) -> CommandResult:
    """Search through activity logs
    
    Usage: /search-logs "create file" --days 3
    """
    try:
        commands = ActivityCommands()
        
        if not query:
            return CommandResult(False, "Search query is required")
        
        # Get activities from specified time window
        activities = commands.reader.read_activities(
            time_window_hours=days * 24,
            max_entries=1000,
            session_filter="all"
        )
        
        # Search through activities
        matches = []
        query_lower = query.lower()
        
        for activity in activities:
            searchable_text = " ".join([
                activity.get('operation', ''),
                activity.get('prompt', ''),
                activity.get('result', '')
            ]).lower()
            
            if query_lower in searchable_text:
                matches.append(activity)
        
        if not matches:
            return CommandResult(True, f"No activities found matching '{query}'")
        
        # Format results
        output = f"# 🔍 Search Results for '{query}' ({len(matches)} matches)\n\n"
        
        for activity in matches[-20:]:  # Show last 20 matches
            timestamp = activity.get('timestamp', 'unknown')
            session_id = activity.get('session_id', 'unknown')
            operation = activity.get('operation', 'unknown')
            prompt = activity.get('prompt', '')
            result = activity.get('result', '')
            
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime("%m-%d %H:%M")
            except:
                time_str = timestamp
            
            output += f"**{time_str}** [{session_id[:8]}] `{operation}`\n"
            if prompt and query_lower in prompt.lower():
                output += f"  💬 {prompt}\n"
            if result and query_lower in result.lower():
                output += f"  ✅ {result}\n"
            output += "\n"
        
        return CommandResult(True, output, matches)
        
    except Exception as e:
        return CommandResult(False, f"Error searching logs: {e}")

def clear_session(**kwargs) -> CommandResult:
    """Start a new session with new session ID
    
    Usage: /clear-session
    """
    try:
        commands = ActivityCommands()
        
        old_session = commands.logger.session_id
        
        # Generate new session ID
        new_session = str(uuid.uuid4())[:8]
        
        # Update environment variable (this affects the current process)
        os.environ['CLAUDE_SESSION_ID'] = new_session
        
        # Create new logger instance with new session
        commands.logger = AgentActivityLogger()
        
        # Log the session change
        commands.logger.log_operation(
            operation="session_start",
            prompt=f"Started new session (previous: {old_session})",
            result=f"New session ID: {new_session}"
        )
        
        output = f"✓ Started new session\n"
        output += f"Previous session: {old_session}\n"
        output += f"New session: {new_session}\n\n"
        output += "The new session ID is now active for all subsequent activities."
        
        return CommandResult(True, output, {"new_session": new_session, "old_session": old_session})
        
    except Exception as e:
        return CommandResult(False, f"Error starting new session: {e}")

def export_session(format: str = "json", output: str = None, **kwargs) -> CommandResult:
    """Export current session logs
    
    Usage: /export-session --format json --output session-export.json
    """
    try:
        commands = ActivityCommands()
        
        activities = commands.reader.read_activities(
            time_window_hours=24 * 7,  # 7 days
            max_entries=10000,
            session_filter="current"
        )
        
        if not activities:
            return CommandResult(True, "No activities to export in current session.")
        
        session_id = commands.logger.session_id
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        if not output:
            output = f"session-{session_id}-{timestamp}.{format}"
        
        export_data = {
            "session_id": session_id,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "total_activities": len(activities),
            "activities": activities
        }
        
        output_path = Path(output)
        
        if format.lower() == "json":
            with open(output_path, 'w') as f:
                json.dump(export_data, f, indent=2)
        
        elif format.lower() == "csv":
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'session_id', 'operation', 'prompt', 'result'])
                for activity in activities:
                    writer.writerow([
                        activity.get('timestamp', ''),
                        activity.get('session_id', ''),
                        activity.get('operation', ''),
                        activity.get('prompt', ''),
                        activity.get('result', '')
                    ])
        
        elif format.lower() == "markdown":
            with open(output_path, 'w') as f:
                f.write(f"# Session Export: {session_id}\n\n")
                f.write(f"**Exported:** {export_data['exported_at']}\n")
                f.write(f"**Total Activities:** {len(activities)}\n\n")
                
                for activity in activities:
                    timestamp = activity.get('timestamp', 'unknown')
                    operation = activity.get('operation', 'unknown')
                    prompt = activity.get('prompt', '')
                    result = activity.get('result', '')
                    
                    f.write(f"## {timestamp} - {operation}\n\n")
                    if prompt:
                        f.write(f"**Prompt:** {prompt}\n\n")
                    if result:
                        f.write(f"**Result:** {result}\n\n")
                    f.write("---\n\n")
        
        else:
            return CommandResult(False, f"Unsupported format: {format}")
        
        result_msg = f"✓ Exported {len(activities)} activities to {output_path}\n"
        result_msg += f"Format: {format.upper()}\n"
        result_msg += f"Session: {session_id}"
        
        return CommandResult(True, result_msg, {"file": str(output_path), "count": len(activities)})
        
    except Exception as e:
        return CommandResult(False, f"Error exporting session: {e}")

def log_config(key: str = None, value: str = None, **kwargs) -> CommandResult:
    """Configure logging settings
    
    Usage: /log-config
    Usage: /log-config --key default_time_window --value 48
    """
    try:
        commands = ActivityCommands()
        
        if key is None and value is None:
            # Show current configuration
            output = "# 🔧 Activity Logging Configuration\n\n"
            for k, v in commands.config.items():
                output += f"**{k}:** {v}\n"
            
            output += "\n**Available settings:**\n"
            output += "• `default_time_window` - Default hours to look back (int)\n"
            output += "• `default_max_entries` - Default max entries to show (int)\n"
            output += "• `auto_log_commands` - Auto-log Claude Code commands (bool)\n" 
            output += "• `output_format` - Output format preference (string)\n"
            
            return CommandResult(True, output, commands.config)
        
        elif key and value is None:
            # Get specific setting
            if key in commands.config:
                return CommandResult(True, f"{key}: {commands.config[key]}")
            else:
                return CommandResult(False, f"Unknown configuration key: {key}")
        
        elif key and value:
            # Set configuration
            # Try to convert value to appropriate type
            if key in ["default_time_window", "default_max_entries"]:
                try:
                    value = int(value)
                except ValueError:
                    return CommandResult(False, f"Value for {key} must be an integer")
            
            elif key == "auto_log_commands":
                value = value.lower() in ["true", "1", "yes", "on"]
            
            commands.config[key] = value
            commands.save_config()
            
            return CommandResult(True, f"✓ Set {key} = {value}")
        
        else:
            return CommandResult(False, "Either provide both key and value, or neither")
            
    except Exception as e:
        return CommandResult(False, f"Error managing configuration: {e}")

# ============================================================================
# MEMORY SYSTEM COMMANDS
# ============================================================================

def memory_status(**kwargs) -> CommandResult:
    """Show status of all memory system files

    Usage: /memory-status
    """
    try:
        memory = get_memory_manager()
        status = memory.get_status()

        output = "# 🧠 Memory System Status\n\n"
        output += f"**Memory Directory:** `{status['memory_dir']}`\n"
        output += f"**Directory Exists:** {status['exists']}\n"
        output += f"**YAML Support:** {status['yaml_available']}\n\n"

        output += "## Memory Files\n\n"
        for filename, file_info in status['files'].items():
            exists_icon = "✅" if file_info['exists'] else "❌"
            output += f"### {exists_icon} {filename}\n"
            output += f"  *{file_info['description']}*\n"

            if file_info['exists']:
                output += f"  - **Size:** {file_info['size_bytes']} bytes\n"
                output += f"  - **Modified:** {file_info['modified']}\n"
            else:
                output += f"  - File not found\n"
            output += "\n"

        return CommandResult(True, output, status)

    except Exception as e:
        return CommandResult(False, f"Error getting memory status: {e}")


def memory_read(file: str = None, **kwargs) -> CommandResult:
    """Read a specific memory file or all files

    Usage: /memory-read --file session_state.yaml
    Usage: /memory-read  (reads all files)
    """
    try:
        memory = get_memory_manager()

        if file:
            # Read specific file
            data = memory.read_file(file)
            if data is None:
                return CommandResult(True, f"File '{file}' not found or empty.")

            if "error" in data:
                return CommandResult(False, f"Error reading file: {data['error']}")

            output = f"# 📄 {file}\n\n"
            output += "```yaml\n"
            if 'raw_content' in data:
                output += data['raw_content']
            else:
                import json
                output += json.dumps(data, indent=2)
            output += "\n```"

            return CommandResult(True, output, data)
        else:
            # Read all files
            all_data = memory.read_all()

            output = "# 🧠 All Memory Files\n\n"
            for filename, data in all_data.items():
                output += f"## {filename}\n\n"
                if data is None:
                    output += "*File not found*\n\n"
                elif "error" in data:
                    output += f"*Error: {data['error']}*\n\n"
                else:
                    output += "```yaml\n"
                    if 'raw_content' in data:
                        output += data['raw_content'][:1000]
                    else:
                        import json
                        output += json.dumps(data, indent=2)[:1000]
                    output += "\n```\n\n"

            return CommandResult(True, output, all_data)

    except Exception as e:
        return CommandResult(False, f"Error reading memory: {e}")


def memory_checkpoint(description: str = "", status: str = "in_progress",
                      project: str = "", **kwargs) -> CommandResult:
    """Create a recovery checkpoint

    Usage: /memory-checkpoint --description "Working on ETL pipeline" --status in_progress
    """
    try:
        memory = get_memory_manager()

        checkpoint_data = {
            "project": {
                "path": str(Path.cwd()),
                "name": project or Path.cwd().name,
                "state": status
            },
            "what_was_working_on": description,
            "context_for_resume": description
        }

        success = memory.create_checkpoint(checkpoint_data)

        if success:
            output = "✓ Recovery checkpoint created\n\n"
            output += f"**Project:** {checkpoint_data['project']['name']}\n"
            output += f"**Status:** {status}\n"
            output += f"**Description:** {description}\n"
            return CommandResult(True, output, checkpoint_data)
        else:
            return CommandResult(False, "Failed to create checkpoint")

    except Exception as e:
        return CommandResult(False, f"Error creating checkpoint: {e}")


def memory_add_task(description: str, priority: str = "medium", **kwargs) -> CommandResult:
    """Add a task to planned tasks

    Usage: /memory-add-task --description "Implement user authentication" --priority high
    """
    try:
        memory = get_memory_manager()

        task = {
            "id": str(uuid.uuid4())[:8],
            "description": description,
            "priority": priority,
            "status": "pending"
        }

        success = memory.add_planned_task(task)

        if success:
            output = f"✓ Task added\n\n"
            output += f"**ID:** {task['id']}\n"
            output += f"**Description:** {description}\n"
            output += f"**Priority:** {priority}\n"
            return CommandResult(True, output, task)
        else:
            return CommandResult(False, "Failed to add task")

    except Exception as e:
        return CommandResult(False, f"Error adding task: {e}")


def memory_complete_task(task_id: str, **kwargs) -> CommandResult:
    """Mark a task as complete (removes from planned tasks)

    Usage: /memory-complete-task --task_id abc123
    """
    try:
        memory = get_memory_manager()

        success = memory.remove_planned_task(task_id)

        if success:
            return CommandResult(True, f"✓ Task {task_id} marked as complete and removed")
        else:
            return CommandResult(False, f"Task {task_id} not found or could not be removed")

    except Exception as e:
        return CommandResult(False, f"Error completing task: {e}")


def memory_log(action: str, details: str = "", **kwargs) -> CommandResult:
    """Add an entry to the work log

    Usage: /memory-log --action "Implemented feature X" --details "Added new API endpoint"
    """
    try:
        memory = get_memory_manager()

        entry = {
            "action": action,
            "details": details,
            "project": Path.cwd().name
        }

        success = memory.add_work_log_entry(entry)

        if success:
            output = f"✓ Work log entry added\n\n"
            output += f"**Action:** {action}\n"
            if details:
                output += f"**Details:** {details}\n"
            return CommandResult(True, output, entry)
        else:
            return CommandResult(False, "Failed to add work log entry")

    except Exception as e:
        return CommandResult(False, f"Error adding work log entry: {e}")


def memory_prime(**kwargs) -> CommandResult:
    """Get full memory system context for agent priming

    Usage: /memory-prime
    """
    try:
        memory = get_memory_manager()
        context = memory.get_recovery_context()

        return CommandResult(True, context, {"context": context})

    except Exception as e:
        return CommandResult(False, f"Error getting prime context: {e}")


# Command registry for Claude Code
COMMANDS = {
    # Activity logging commands
    "log-activity": log_activity,
    "show-history": show_history,
    "session-summary": session_summary,
    "search-logs": search_logs,
    "clear-session": clear_session,
    "export-session": export_session,
    "log-config": log_config,
    # Memory system commands
    "memory-status": memory_status,
    "memory-read": memory_read,
    "memory-checkpoint": memory_checkpoint,
    "memory-add-task": memory_add_task,
    "memory-complete-task": memory_complete_task,
    "memory-log": memory_log,
    "memory-prime": memory_prime
}

def execute_command(command_name: str, **kwargs) -> CommandResult:
    """Execute a command by name"""
    if command_name in COMMANDS:
        return COMMANDS[command_name](**kwargs)
    else:
        return CommandResult(False, f"Unknown command: {command_name}")