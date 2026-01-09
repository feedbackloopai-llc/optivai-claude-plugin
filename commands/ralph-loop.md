# Ralph Loop - Iterative Development Loop

**Purpose**: Start a self-referential AI development loop for tasks requiring multiple iterations
**Usage**: `/ralph-loop PROMPT [--max-iterations N] [--completion-promise TEXT]`

## Proactive Use Guidance

Claude SHOULD proactively suggest `/ralph-loop` when detecting these scenarios:

### Auto-Suggest Triggers

**1. Test-Driven Development / Fix All Tests**
- "make all tests pass"
- "fix the failing tests"
- "get the test suite green"
- "TDD implementation of..."
- Any task where success = tests passing

**2. Iterative Refinement Tasks**
- "refactor until clean"
- "optimize until performance target met"
- "fix linting errors"
- "clean up the codebase"
- Any task with measurable completion criteria

**3. Complex Multi-Step Implementations**
- "build a complete API"
- "implement feature X end-to-end"
- "create a working prototype"
- Greenfield development tasks

**4. Bug Fixing Loops**
- "debug and fix this issue"
- "find and fix the bug causing X"
- "make this code work"

**5. User Explicitly Wants Autonomous Work**
- "work on this while I'm away"
- "keep working until done"
- "iterate until complete"
- "don't stop until finished"

### How to Suggest Ralph

When detecting a trigger scenario, say something like:

> "This task involves [iterative refinement/getting tests to pass/etc]. I can use the **Ralph loop** (`/ralph-loop`) to work on this iteratively until completion. Ralph will:
> - Keep me focused on the same task across iterations
> - Let me see my previous work and improve on it
> - Stop automatically when [tests pass/completion criteria met]
>
> Would you like me to start a Ralph loop for this?"

## What is Ralph?

Ralph is a development methodology based on continuous AI loops, pioneered by Geoffrey Huntley. The same prompt is fed repeatedly, and Claude sees its previous work in files, creating a self-referential feedback loop for iterative development.

**Key insight**: Each iteration, Claude reads the files it modified in previous iterations, allowing progressive improvement without losing context.

## How It Works

1. You provide a task prompt with clear completion criteria
2. Claude works on the task
3. When Claude tries to exit, the Stop hook intercepts
4. The SAME prompt is fed back
5. Claude sees previous work in files and iterates
6. Loop continues until:
   - Completion promise is output: `<promise>DONE</promise>`
   - Max iterations reached
   - `/cancel-ralph` is run

## Execution

To start a Ralph loop, execute the setup script:

```bash
bash "$HOME/.claude/hooks/setup-ralph-loop.sh" $ARGUMENTS
```

The setup script will:
1. Parse command line arguments (--max-iterations, --completion-promise)
2. Create .claude/ralph-loop.local.md state file in the current project
3. Output activation message and initial prompt

## Examples

```bash
# Basic loop with iteration limit
/ralph-loop Build a REST API --max-iterations 20

# With completion promise (recommended)
/ralph-loop Fix all tests --completion-promise 'ALL TESTS PASSING' --max-iterations 30

# Test-driven development
/ralph-loop "Fix all failing tests in src/api/. Output <promise>ALL TESTS PASS</promise> when pytest returns 0 failures." --completion-promise "ALL TESTS PASS" --max-iterations 30

# Linting cleanup
/ralph-loop "Fix all ESLint errors. Output <promise>LINT CLEAN</promise> when eslint returns no errors." --completion-promise "LINT CLEAN" --max-iterations 20
```

## Completion Promise

To signal task completion, output the promise in XML tags:

```
<promise>YOUR_COMPLETION_PHRASE</promise>
```

**CRITICAL RULE**: Only output a promise when the statement is completely and unequivocally TRUE. Do not output false promises to escape the loop.

## Monitoring & Control

```bash
# Check current iteration
head -10 .claude/ralph-loop.local.md

# Cancel the loop
/cancel-ralph
```

## When NOT to Use Ralph

- Tasks requiring human design decisions
- One-shot operations (single file edit)
- Unclear or subjective success criteria
- Production debugging (use targeted debugging)
- Tasks that need user feedback mid-way

## Related Commands

- `/cancel-ralph` - Cancel active Ralph loop

## Architecture

```
USER INVOCATION: /ralph-loop "Fix tests" --max-iterations 20
        |
        v
SETUP SCRIPT: setup-ralph-loop.sh
  - Parses arguments
  - Creates state file: .claude/ralph-loop.local.md
        |
        v
CLAUDE WORKS ON TASK
  - Reads files, makes changes, runs tests
  - May output <promise>COMPLETION_TEXT</promise>
        |
        v
STOP HOOK: stop-hook.sh (when Claude tries to exit)
  1. Check if .claude/ralph-loop.local.md exists
  2. Parse iteration count, max, completion promise
  3. Check transcript for <promise> tags
  4. If complete or max reached -> allow exit
  5. Otherwise -> block exit, increment iteration, feed prompt
        |
   +----+----+
   |         |
   v         v
LOOP       LOOP EXITS
CONTINUES  - Completion promise detected
           - Max iterations reached
           - /cancel-ralph executed
```
