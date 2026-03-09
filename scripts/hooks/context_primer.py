#!/usr/bin/env python3
"""
Context Primer - Load recent session activity for agent context priming

This utility reads local logs and prepares context for a new Claude session,
allowing the agent to understand recent activity and continue work seamlessly.

Usage:
    python3 context_primer.py                    # Last 20 entries from current project
    python3 context_primer.py --limit 50         # Last 50 entries
    python3 context_primer.py --session SESSION  # Specific session
    python3 context_primer.py --format markdown  # Markdown output
    python3 context_primer.py --files-only       # Just list files accessed
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional


def get_log_dir() -> Path:
    """Get the log directory for current project"""
    project_dir = Path.cwd()
    return project_dir / ".claude" / "logs"


def get_today_log_file() -> Optional[Path]:
    """Get today's log file"""
    log_dir = get_log_dir()
    date = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"agent-activity-{date}.log"
    return log_file if log_file.exists() else None


def load_recent_entries(limit: int = 20, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load recent log entries"""
    log_file = get_today_log_file()
    if not log_file:
        return []

    entries = []
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if session_id and entry.get('session_id') != session_id:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error reading log file: {e}", file=sys.stderr)
        return []

    # Return last N entries
    return entries[-limit:]


def get_files_accessed(entries: List[Dict[str, Any]]) -> List[str]:
    """Extract unique files accessed from entries"""
    files = set()
    for entry in entries:
        details = entry.get('details', {})
        if 'file_path' in details:
            files.add(details['file_path'])
    return sorted(files)


def get_sessions(entries: List[Dict[str, Any]]) -> List[str]:
    """Extract unique session IDs"""
    sessions = set()
    for entry in entries:
        if 'session_id' in entry:
            sessions.add(entry['session_id'])
    return sorted(sessions)


def format_as_markdown(entries: List[Dict[str, Any]]) -> str:
    """Format entries as markdown for easy reading"""
    if not entries:
        return "No recent activity found."

    lines = ["# Recent Session Activity\n"]

    # Summary
    files = get_files_accessed(entries)
    sessions = get_sessions(entries)

    lines.append(f"**Entries:** {len(entries)}")
    lines.append(f"**Sessions:** {len(sessions)}")
    lines.append(f"**Files Accessed:** {len(files)}")
    lines.append("")

    # Files list
    if files:
        lines.append("## Files Accessed")
        for f in files[:20]:  # Limit to 20 files
            lines.append(f"- `{f}`")
        if len(files) > 20:
            lines.append(f"- ... and {len(files) - 20} more")
        lines.append("")

    # Recent operations
    lines.append("## Recent Operations")
    for entry in entries[-10:]:  # Last 10 operations
        op = entry.get('operation', 'unknown')
        prompt = entry.get('prompt', '')[:80]
        time = entry.get('time', '')
        lines.append(f"- **{time}** `{op}`: {prompt}")

    return "\n".join(lines)


def format_as_json(entries: List[Dict[str, Any]]) -> str:
    """Format entries as JSON"""
    return json.dumps(entries, indent=2)


def format_as_context(entries: List[Dict[str, Any]]) -> str:
    """Format entries as context for agent priming"""
    if not entries:
        return "No recent activity to prime context."

    lines = ["<recent_session_context>"]

    # Session info
    if entries:
        first = entries[0]
        lines.append(f"Project: {first.get('project', 'unknown')}")
        lines.append(f"User: {first.get('user', 'unknown')}")

        bedrock = first.get('bedrock', {})
        if bedrock.get('is_bedrock'):
            lines.append(f"Environment: Bedrock ({bedrock.get('aws_region', 'unknown')})")
            lines.append(f"Model: {bedrock.get('model', 'unknown')}")

    # Files worked on
    files = get_files_accessed(entries)
    if files:
        lines.append(f"\nRecently accessed files ({len(files)}):")
        for f in files[:15]:
            lines.append(f"  - {f}")
        if len(files) > 15:
            lines.append(f"  - ... and {len(files) - 15} more")

    # Recent prompts (for conversation context)
    user_prompts = [e for e in entries if e.get('operation') == 'user_prompt']
    if user_prompts:
        lines.append(f"\nRecent user prompts:")
        for p in user_prompts[-5:]:
            prompt_text = p.get('prompt', '')[:150]
            lines.append(f"  - {prompt_text}")

    # Recent tool operations
    tool_ops = [e for e in entries if e.get('operation') != 'user_prompt'][-10:]
    if tool_ops:
        lines.append(f"\nRecent operations:")
        for op in tool_ops:
            lines.append(f"  - {op.get('operation')}: {op.get('prompt', '')[:60]}")

    lines.append("</recent_session_context>")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Load recent session activity for context priming",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--limit", "-n", type=int, default=20, help="Number of entries to load")
    parser.add_argument("--session", "-s", type=str, help="Filter by session ID")
    parser.add_argument("--format", "-f", choices=["json", "markdown", "context", "files"],
                        default="context", help="Output format")
    parser.add_argument("--files-only", action="store_true", help="Only list files accessed")

    args = parser.parse_args()

    entries = load_recent_entries(limit=args.limit, session_id=args.session)

    if args.files_only or args.format == "files":
        files = get_files_accessed(entries)
        for f in files:
            print(f)
    elif args.format == "json":
        print(format_as_json(entries))
    elif args.format == "markdown":
        print(format_as_markdown(entries))
    else:
        print(format_as_context(entries))


if __name__ == "__main__":
    main()
