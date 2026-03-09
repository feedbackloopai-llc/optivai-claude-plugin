#!/usr/bin/env python3
"""
ABOUTME: Ralph Wiggum Stop Hook for OptivAI Claude Plugin (Python version)
ABOUTME: Prevents session exit when a ralph-loop is active, creating self-referential development loops
ABOUTME: Cross-platform compatible - works on Windows, Mac, and Linux
"""

import sys
import os
import json
import re
from pathlib import Path


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown file."""
    lines = content.split('\n')
    if not lines or lines[0].strip() != '---':
        return {}

    frontmatter = {}
    in_frontmatter = False
    for line in lines:
        if line.strip() == '---':
            if in_frontmatter:
                break  # End of frontmatter
            in_frontmatter = True
            continue

        if in_frontmatter and ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            # Remove surrounding quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            frontmatter[key] = value

    return frontmatter


def get_prompt_text(content: str) -> str:
    """Extract prompt text (everything after the closing ---)."""
    lines = content.split('\n')
    dash_count = 0
    prompt_lines = []

    for line in lines:
        if line.strip() == '---':
            dash_count += 1
            continue
        if dash_count >= 2:
            prompt_lines.append(line)

    return '\n'.join(prompt_lines).strip()


def extract_promise_text(output: str) -> str:
    """Extract text from <promise> tags."""
    match = re.search(r'<promise>(.*?)</promise>', output, re.DOTALL)
    if match:
        # Normalize whitespace
        text = match.group(1).strip()
        text = re.sub(r'\s+', ' ', text)
        return text
    return ""


def main():
    # Read hook input from stdin
    try:
        hook_input = sys.stdin.read()
        hook_data = json.loads(hook_input) if hook_input.strip() else {}
    except json.JSONDecodeError:
        hook_data = {}

    # Check if ralph-loop is active
    ralph_state_file = Path.cwd() / ".claude" / "ralph-loop.local.md"

    if not ralph_state_file.exists():
        # No active loop - allow exit
        sys.exit(0)

    try:
        content = ralph_state_file.read_text(encoding='utf-8')
    except Exception as e:
        print(f"Warning: Could not read Ralph state file: {e}", file=sys.stderr)
        sys.exit(0)

    # Parse frontmatter
    frontmatter = parse_frontmatter(content)

    try:
        iteration = int(frontmatter.get('iteration', '0'))
    except ValueError:
        print("Warning: Ralph loop state file corrupted (iteration field)", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    try:
        max_iterations = int(frontmatter.get('max_iterations', '0'))
    except ValueError:
        print("Warning: Ralph loop state file corrupted (max_iterations field)", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    completion_promise = frontmatter.get('completion_promise', '')

    # Check if max iterations reached
    if max_iterations > 0 and iteration >= max_iterations:
        print(f"Ralph loop: Max iterations ({max_iterations}) reached.")
        ralph_state_file.unlink()
        sys.exit(0)

    # Get transcript path from hook input
    transcript_path = hook_data.get('transcript_path', '')

    if not transcript_path or not Path(transcript_path).exists():
        print("Warning: Ralph loop transcript file not found", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    # Read transcript and find last assistant message
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Warning: Could not read transcript: {e}", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    # Find last assistant message (JSONL format)
    last_assistant_line = None
    for line in reversed(lines):
        if '"role":"assistant"' in line or '"role": "assistant"' in line:
            last_assistant_line = line
            break

    if not last_assistant_line:
        print("Warning: No assistant messages found in transcript", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    # Parse the assistant message
    try:
        msg_data = json.loads(last_assistant_line)
        content_parts = msg_data.get('message', {}).get('content', [])
        text_parts = [p.get('text', '') for p in content_parts if p.get('type') == 'text']
        last_output = '\n'.join(text_parts)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: Failed to parse assistant message JSON: {e}", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    if not last_output:
        print("Warning: Assistant message contained no text content", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    # Check for completion promise
    if completion_promise and completion_promise != 'null':
        promise_text = extract_promise_text(last_output)
        if promise_text and promise_text == completion_promise:
            print(f"Ralph loop: Detected <promise>{completion_promise}</promise> - Task complete!")
            ralph_state_file.unlink()
            sys.exit(0)

    # Not complete - continue loop with SAME PROMPT
    next_iteration = iteration + 1

    # Extract prompt text
    prompt_text = get_prompt_text(content)

    if not prompt_text:
        print("Warning: Ralph loop state file has no prompt text", file=sys.stderr)
        ralph_state_file.unlink()
        sys.exit(0)

    # Update iteration in state file
    updated_content = re.sub(
        r'^iteration:\s*\d+',
        f'iteration: {next_iteration}',
        content,
        flags=re.MULTILINE
    )
    ralph_state_file.write_text(updated_content, encoding='utf-8')

    # Build system message
    if completion_promise and completion_promise != 'null':
        system_msg = f"Ralph iteration {next_iteration} | To complete: output <promise>{completion_promise}</promise> (ONLY when TRUE)"
    else:
        system_msg = f"Ralph iteration {next_iteration} | No completion promise set - loop runs until max iterations"

    # Output JSON to block the stop and feed prompt back
    result = {
        "decision": "block",
        "reason": prompt_text,
        "systemMessage": system_msg
    }
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
