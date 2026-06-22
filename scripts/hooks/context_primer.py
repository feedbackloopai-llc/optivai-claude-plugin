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
import subprocess
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


def _open_brain_path() -> Path:
    """Resolve the open_brain.py script path (sibling of this file)."""
    return Path(__file__).resolve().parent / "open_brain.py"


def _normalize_atom(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Reduce a raw open_brain.py JSON record to the fields we render.

    open_brain.py emits UPPERCASE keys (THOUGHT_ID, SUMMARY, THOUGHT_TYPE,
    CREATED_AT). We also accept lowercase aliases (id, summary, type, date)
    in case the shape ever evolves. Records missing an id are dropped.
    """
    atom_id = raw.get("THOUGHT_ID") or raw.get("id")
    if not atom_id:
        return None
    summary = (
        raw.get("SUMMARY")
        or raw.get("summary")
        or raw.get("RAW_TEXT")
        or ""
    )
    thought_type = raw.get("THOUGHT_TYPE") or raw.get("type") or "atom"
    created_at = raw.get("CREATED_AT") or raw.get("date") or ""
    # Trim summary so block stays compact (one-line per atom).
    summary = " ".join(str(summary).split())
    if len(summary) > 140:
        summary = summary[:137] + "..."
    # Date column: keep just YYYY-MM-DD if possible.
    date_str = str(created_at)
    if len(date_str) >= 10 and date_str[4] == "-" and date_str[7] == "-":
        date_str = date_str[:10]
    return {
        "id": str(atom_id),
        "summary": summary,
        "type": str(thought_type),
        "date": date_str,
    }


def _format_atom_line(atom: Dict[str, str]) -> str:
    """Render one atom as a bullet line for the Markdown block."""
    return f"- {atom['date']} | {atom['type']} | {atom['id']} — {atom['summary']}"


def _run_open_brain(args: List[str], timeout: int = 8) -> List[Dict[str, Any]]:
    """Shell open_brain.py with the given args and return parsed JSON list.

    Raises subprocess.CalledProcessError, subprocess.TimeoutExpired, or
    json.JSONDecodeError on failure — caller is responsible for fail-open.
    """
    brain_script = _open_brain_path()
    cmd = ["python3", str(brain_script)] + args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, list):
        raise json.JSONDecodeError("expected list", proc.stdout, 0)
    return parsed


def enrich_with_brain_context(
    working_dir: str,
    days: int = 3,
    recall_k: int = 5,
) -> str:
    """Return a Markdown block of recent + project-relevant brain atoms.

    Two subprocess calls (each capped at 8s):
      1. open_brain.py --recent --days <days> --json --limit 10
      2. open_brain.py --search "<basename of working_dir>" --json --limit <recall_k>

    Atoms are deduplicated by id and the combined output is capped at 15
    atoms. Fail-open semantics: any subprocess error, timeout, JSON parse
    failure, or generic exception returns "" and logs a one-line warning
    to stderr — this function MUST never raise and MUST never block
    session priming.
    """
    try:
        basename = os.path.basename(working_dir.rstrip("/")) or working_dir
    except Exception as exc:  # pragma: no cover — defensive only
        print(f"warn: brain enrich basename failed: {exc}", file=sys.stderr)
        return ""

    recent_raw: List[Dict[str, Any]] = []
    search_raw: List[Dict[str, Any]] = []

    try:
        recent_raw = _run_open_brain(
            ["--recent", "--days", str(days), "--json", "--limit", "10"],
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        print("warn: brain --recent timed out (8s); skipping brain block",
              file=sys.stderr)
        return ""
    except subprocess.CalledProcessError as exc:
        print(f"warn: brain --recent exited {exc.returncode}; skipping brain block",
              file=sys.stderr)
        return ""
    except json.JSONDecodeError as exc:
        print(f"warn: brain --recent JSON parse failed: {exc}; skipping brain block",
              file=sys.stderr)
        return ""
    except Exception as exc:
        print(f"warn: brain --recent unexpected error: {exc}; skipping brain block",
              file=sys.stderr)
        return ""

    try:
        search_raw = _run_open_brain(
            ["--search", basename, "--json", "--limit", str(recall_k)],
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        print("warn: brain --search timed out (8s); rendering recent-only block",
              file=sys.stderr)
        search_raw = []
    except subprocess.CalledProcessError as exc:
        print(f"warn: brain --search exited {exc.returncode}; rendering recent-only block",
              file=sys.stderr)
        search_raw = []
    except json.JSONDecodeError as exc:
        print(f"warn: brain --search JSON parse failed: {exc}; rendering recent-only block",
              file=sys.stderr)
        search_raw = []
    except Exception as exc:
        print(f"warn: brain --search unexpected error: {exc}; rendering recent-only block",
              file=sys.stderr)
        search_raw = []

    # Normalize + dedup. Recent atoms are added first (priority); project-
    # relevant atoms only appear if their id was not already seen.
    seen_ids: set = set()
    recent_atoms: List[Dict[str, str]] = []
    for raw in recent_raw:
        atom = _normalize_atom(raw)
        if atom is None or atom["id"] in seen_ids:
            continue
        seen_ids.add(atom["id"])
        recent_atoms.append(atom)

    search_atoms: List[Dict[str, str]] = []
    for raw in search_raw:
        atom = _normalize_atom(raw)
        if atom is None or atom["id"] in seen_ids:
            continue
        seen_ids.add(atom["id"])
        search_atoms.append(atom)

    # Cap combined output at 15 atoms; trim project-relevant first so the
    # recent slice is preserved.
    total_cap = 15
    if len(recent_atoms) + len(search_atoms) > total_cap:
        remaining = max(0, total_cap - len(recent_atoms))
        search_atoms = search_atoms[:remaining]
        recent_atoms = recent_atoms[:total_cap]

    if not recent_atoms and not search_atoms:
        return ""

    lines: List[str] = ["## Recent neurosymbolic context", ""]

    lines.append(f"### Last 3 days (top {len(recent_atoms)})")
    if recent_atoms:
        for atom in recent_atoms:
            lines.append(_format_atom_line(atom))
    else:
        lines.append("- (no recent atoms returned)")
    lines.append("")

    lines.append(f"### Project-relevant ({basename})")
    if search_atoms:
        for atom in search_atoms:
            lines.append(_format_atom_line(atom))
    else:
        lines.append("- (no project-relevant atoms returned)")

    return "\n".join(lines)


def format_as_context(entries: List[Dict[str, Any]]) -> str:
    """Format entries as context for agent priming"""
    if not entries:
        base = "No recent activity to prime context."
        brain_block = enrich_with_brain_context(os.getcwd())
        if brain_block:
            return base + "\n\n" + brain_block
        return base

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

    base_output = "\n".join(lines)

    # Append the neurosymbolic brain block (recent atoms + project-scoped
    # recall). Fail-open: empty string if the brain backend is unreachable.
    project_dir = None
    if entries:
        project_dir = entries[0].get("project")
    brain_block = enrich_with_brain_context(project_dir or os.getcwd())
    if brain_block:
        return base_output + "\n\n" + brain_block
    return base_output


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
