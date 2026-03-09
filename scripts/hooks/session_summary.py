#!/usr/bin/env python3
"""
ABOUTME: Session Summary Stop Hook for OptivAI Claude Plugin
ABOUTME: Parses Claude Code transcript JSONL to extract token usage per session
ABOUTME: Writes a SESSION_SUMMARY event to the activity log for PostgreSQL sync
ABOUTME: Cross-platform compatible - uses pathlib throughout
"""

import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional


# Cost per 1M tokens by model family (based on Anthropic public pricing)
# Cache write = 1.25x input rate, cache read = 0.10x input rate
COST_PER_MODEL = {
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4": {"input": 0.80, "output": 4.0, "cache_write": 1.00, "cache_read": 0.08},
    "default": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
}


def get_model_family(model_id: str) -> str:
    """Extract model family from model ID for cost lookup.

    Examples:
        claude-opus-4-5-20251101 -> claude-opus-4
        claude-sonnet-4-6 -> claude-sonnet-4
        claude-haiku-4-5-20251001 -> claude-haiku-4
    """
    model_lower = model_id.lower()
    if "opus" in model_lower:
        return "claude-opus-4"
    if "sonnet" in model_lower:
        return "claude-sonnet-4"
    if "haiku" in model_lower:
        return "claude-haiku-4"
    return "default"


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    model_id: str,
) -> float:
    """Estimate API-equivalent cost in USD based on token counts and model.

    Note: For Teams/Enterprise subscriptions, actual billing is subscription-based.
    This estimate reflects what the equivalent API usage would cost.
    """
    family = get_model_family(model_id)
    rates = COST_PER_MODEL.get(family, COST_PER_MODEL["default"])
    input_cost = (input_tokens / 1_000_000) * rates["input"]
    output_cost = (output_tokens / 1_000_000) * rates["output"]
    cache_write_cost = (cache_creation_tokens / 1_000_000) * rates["cache_write"]
    cache_read_cost = (cache_read_tokens / 1_000_000) * rates["cache_read"]
    return round(input_cost + output_cost + cache_write_cost + cache_read_cost, 4)


def extract_session_tokens(transcript_path: str) -> Optional[Dict[str, Any]]:
    """Parse transcript JSONL, sum token usage from all assistant messages.

    Returns None if transcript is empty or unreadable.
    """
    path = Path(transcript_path)
    if not path.exists():
        return None

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
        "api_calls": 0,
        "model": "unknown",
        "models_used": set(),
        "first_timestamp": None,
        "last_timestamp": None,
    }

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Track first/last entry timestamps for session duration
                entry_ts = obj.get("timestamp")
                if entry_ts:
                    if totals["first_timestamp"] is None:
                        totals["first_timestamp"] = entry_ts
                    totals["last_timestamp"] = entry_ts

                if obj.get("type") != "assistant":
                    continue

                msg = obj.get("message", {})
                usage = msg.get("usage", {})

                if not usage:
                    continue

                totals["input_tokens"] += usage.get("input_tokens", 0)
                totals["output_tokens"] += usage.get("output_tokens", 0)
                totals["cache_creation_input_tokens"] += usage.get(
                    "cache_creation_input_tokens", 0
                )
                totals["cache_read_input_tokens"] += usage.get(
                    "cache_read_input_tokens", 0
                )
                totals["api_calls"] += 1

                model = msg.get("model")
                if model:
                    totals["models_used"].add(model)
                    totals["model"] = model  # last model used

    except Exception:
        return None

    if totals["api_calls"] == 0:
        return None

    totals["total_tokens"] = (
        totals["input_tokens"]
        + totals["output_tokens"]
        + totals["cache_creation_input_tokens"]
        + totals["cache_read_input_tokens"]
    )
    totals["models_used"] = sorted(list(totals["models_used"]))
    totals["estimated_cost_usd"] = estimate_cost(
        totals["input_tokens"],
        totals["output_tokens"],
        totals["cache_creation_input_tokens"],
        totals["cache_read_input_tokens"],
        totals["model"],
    )
    return totals


def get_provider_env() -> Dict[str, str]:
    """Detect provider environment (mirrors log_writer._get_provider_env)."""
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
        return {
            "type": "bedrock",
            "model": os.environ.get("ANTHROPIC_MODEL", "unknown"),
            "aws_region": os.environ.get("AWS_REGION", ""),
        }
    return {
        "type": "teams",
        "model": os.environ.get("ANTHROPIC_MODEL", "unknown"),
        "organization": os.environ.get("CLAUDE_ORG_NAME", "FeedbackLoopAI"),
        "user_email": os.environ.get("CLAUDE_USER_EMAIL", ""),
    }


def get_user() -> str:
    """Get current OS username."""
    for var in ["USER", "USERNAME", "LOGNAME"]:
        user = os.environ.get(var)
        if user:
            return user
    return "unknown"


def get_log_dir() -> Path:
    """Get global activity log directory."""
    # Try loading config for log path, default to global
    config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    log_dir = Path.home() / ".claude" / "logs"

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            mode = config.get("log_directory_mode", "global")
            if mode == "global":
                global_path = config.get("global_log_path", "")
                if global_path:
                    log_dir = Path(global_path).expanduser()
        except Exception:
            pass

    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def write_session_summary(hook_data: Dict[str, Any]) -> None:
    """Parse transcript and write SESSION_SUMMARY event to activity log."""
    transcript_path = hook_data.get("transcript_path", "")
    session_id = hook_data.get("session_id", "")

    if not transcript_path:
        return

    # Extract token totals from transcript
    totals = extract_session_tokens(transcript_path)
    if totals is None:
        return

    # Calculate session duration from first/last transcript entry timestamps
    session_duration = 0
    try:
        first_ts = totals.pop("first_timestamp", None)
        last_ts = totals.pop("last_timestamp", None)
        if first_ts and last_ts:
            from datetime import datetime as _dt

            # Parse ISO timestamps (handles both Z suffix and +00:00 offset)
            first = _dt.fromisoformat(first_ts.replace("Z", "+00:00"))
            last = _dt.fromisoformat(last_ts.replace("Z", "+00:00"))
            session_duration = int((last - first).total_seconds())
    except (ValueError, TypeError):
        pass

    totals["session_duration_seconds"] = max(session_duration, 0)

    now = datetime.now(timezone.utc)
    now_local = datetime.now()
    user = get_user()
    provider = get_provider_env()

    # Override model from transcript data (more accurate than env var)
    if totals.get("model") and totals["model"] != "unknown":
        provider["model"] = totals["model"]

    # Build summary description
    cost_str = f"${totals['estimated_cost_usd']:.2f}"
    summary_text = (
        f"Session summary: {totals['total_tokens']:,} total tokens "
        f"(in:{totals['input_tokens']:,} out:{totals['output_tokens']:,} "
        f"cache_w:{totals['cache_creation_input_tokens']:,} "
        f"cache_r:{totals['cache_read_input_tokens']:,}), "
        f"{totals['api_calls']} API calls, {cost_str} API-equiv cost"
    )

    # Build log entry matching log_writer.py format
    log_entry = {
        "epoch": int(time.time()),
        "date": now_local.strftime("%Y-%m-%d"),
        "year": now_local.year,
        "month": now_local.month,
        "day": now_local.day,
        "hour": now_local.hour,
        "timestamp": now.isoformat(),
        "time": now_local.strftime("%H:%M:%S"),
        "operation": "session_summary",
        "prompt": summary_text,
        "session_id": session_id,
        "agent_id": f"{Path.cwd().name}/crew/{user}",
        "user": user,
        "cwd": str(Path.cwd()),
        "project": Path.cwd().name,
        "provider": provider,
        "details": {
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "cache_creation_input_tokens": totals["cache_creation_input_tokens"],
            "cache_read_input_tokens": totals["cache_read_input_tokens"],
            "total_tokens": totals["total_tokens"],
            "api_calls": totals["api_calls"],
            "model": totals["model"],
            "models_used": totals["models_used"],
            "estimated_cost_usd": totals["estimated_cost_usd"],
            "session_duration_seconds": totals["session_duration_seconds"],
        },
    }

    # Write to activity log
    log_dir = get_log_dir()
    log_file = log_dir / f"agent-activity-{now_local.strftime('%Y-%m-%d')}.log"

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"session_summary: Failed to write log: {e}", file=sys.stderr)


def main():
    """Entry point - reads hook input from stdin, writes session summary."""
    try:
        hook_input = sys.stdin.read()
        hook_data = json.loads(hook_input) if hook_input.strip() else {}
    except json.JSONDecodeError:
        hook_data = {}

    write_session_summary(hook_data)

    # Always allow exit (don't block the Stop event)
    sys.exit(0)


if __name__ == "__main__":
    main()
