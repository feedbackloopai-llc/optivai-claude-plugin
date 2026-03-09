---
description: "Start Ralph Wiggum iterative development loop - USE THIS when task requires multiple iterations to complete (tests to pass, code to refactor, bugs to fix iteratively)"
argument-hint: "PROMPT [--max-iterations N] [--completion-promise TEXT]"
allowed-tools: ["Bash($PROJECT_DIR/scripts/setup-ralph-loop.sh:*)"]
---

# Ralph Loop Command

**Proactive Use Guidance**: Claude SHOULD suggest `/ralph-loop` when detecting these scenarios:

## Auto-Suggest Triggers

Suggest Ralph when the user's request matches ANY of these patterns:

### 1. Test-Driven Development / Fix All Tests
- "make all tests pass"
- "fix the failing tests"
- "get the test suite green"
- "TDD implementation of..."
- Any task where success = tests passing

**Example suggestion**: "This task involves getting tests to pass iteratively. I recommend using `/ralph-loop` to automatically retry until all tests are green."

### 2. Iterative Refinement Tasks
- "refactor until clean"
- "optimize until performance target met"
- "fix linting errors"
- "clean up the codebase"
- Any task with measurable completion criteria

### 3. Complex Multi-Step Implementations
- "build a complete API"
- "implement feature X end-to-end"
- "create a working prototype"
- Greenfield development tasks

### 4. Bug Fixing Loops
- "debug and fix this issue"
- "find and fix the bug causing X"
- "make this code work"

### 5. User Explicitly Wants Autonomous Work
- "work on this while I'm away"
- "keep working until done"
- "iterate until complete"
- "don't stop until finished"

## How to Suggest Ralph

When detecting a trigger scenario, say something like:

> "This task involves [iterative refinement/getting tests to pass/etc]. I can use the **Ralph loop** (`/ralph-loop`) to work on this iteratively until completion. Ralph will:
> - Keep me focused on the same task across iterations
> - Let me see my previous work and improve on it
> - Stop automatically when [tests pass/completion criteria met]
>
> Would you like me to start a Ralph loop for this? I'd suggest:
> ```
> /ralph-loop "Your task description" --max-iterations 20 --completion-promise "DONE"
> ```"

---

## Command Reference

Execute the setup script to initialize the Ralph loop:

```bash
"$PROJECT_DIR/scripts/setup-ralph-loop.sh" $ARGUMENTS
```

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

## After Starting

Please work on the task. When you try to exit, the Ralph loop will feed the SAME PROMPT back to you for the next iteration. You'll see your previous work in files and git history, allowing you to iterate and improve.

**CRITICAL RULE**: If a completion promise is set, you may ONLY output it when the statement is completely and unequivocally TRUE. Do not output false promises to escape the loop.

## Best Practices for Prompts

### Good Prompts (Clear Completion Criteria)
```bash
# Test-driven
/ralph-loop "Fix all failing tests in src/api/. Output <promise>ALL TESTS PASS</promise> when pytest returns 0 failures." --completion-promise "ALL TESTS PASS" --max-iterations 30

# Linting
/ralph-loop "Fix all ESLint errors. Output <promise>LINT CLEAN</promise> when eslint returns no errors." --completion-promise "LINT CLEAN" --max-iterations 20

# Feature implementation
/ralph-loop "Implement user authentication with login, logout, and password reset. Write tests for each. Output <promise>FEATURE COMPLETE</promise> when all tests pass." --completion-promise "FEATURE COMPLETE" --max-iterations 50
```

### Bad Prompts (Avoid)
```bash
# Too vague - no clear completion
/ralph-loop "Make the code better"

# No measurable success criteria
/ralph-loop "Improve performance"

# Requires human judgment
/ralph-loop "Make the UI look nice"
```

## Examples

```bash
# Basic loop with iteration limit
/ralph-loop Build a REST API --max-iterations 20

# With completion promise (recommended)
/ralph-loop Fix all tests --completion-promise 'ALL TESTS PASSING' --max-iterations 30

# Unlimited iterations (use with caution!)
/ralph-loop Refactor the cache layer
```

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
