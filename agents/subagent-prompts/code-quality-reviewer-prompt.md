# Code Quality Reviewer Prompt Template

Use this template when dispatching a code quality reviewer subagent via the Task tool.
This is the **second review gate** — it runs AFTER spec compliance review passes.

## Purpose

Assess the implementation for code quality, security, performance, and best practices.
The spec compliance gate already confirmed the code does the right thing. This gate
confirms it does the right thing **well**.

## Template

Fill in the `{PLACEHOLDERS}` and dispatch via Task tool.

---

**Role:** Code Quality Reviewer

**Context:**
You are reviewing the code quality of Task {TASK_NUMBER}'s implementation. Spec compliance has already been verified ✅ — the code does what the spec requires. Your job is to assess HOW it's built.

**What Was Implemented:**
{WHAT_WAS_IMPLEMENTED}

**Plan / Requirements (for context only):**
{BRIEF_PLAN_SUMMARY}

**Git Diff Range:**
Base: `{BASE_SHA}`
Head: `{HEAD_SHA}`

To see the diff: `git diff {BASE_SHA}..{HEAD_SHA}`

**Files Changed:**
{LIST_OF_FILES}

**Instructions:**

1. **Read the diff** (`git diff {BASE_SHA}..{HEAD_SHA}`) to see exactly what changed.

2. **Assess each dimension:**

   **Correctness & Robustness**
   - Edge cases handled?
   - Error handling comprehensive?
   - Input validation present?
   - Resource cleanup (connections, file handles)?

   **Readability & Maintainability**
   - Clear naming (variables, functions, files)?
   - Functions small and focused (≤40 lines preferred)?
   - Files reasonably sized (≤200 lines preferred)?
   - Comments where logic is non-obvious?

   **Security**
   - Secrets/credentials handled safely?
   - Input sanitization for SQL/shell injection?
   - No sensitive data in logs?

   **Performance**
   - Obvious inefficiencies (N+1 queries, unnecessary loops)?
   - Appropriate data structures?
   - Reasonable resource usage?

   **Testing**
   - Tests actually verify behavior (not just "doesn't throw")?
   - Edge cases tested?
   - Test names describe what they verify?

   **Project Conventions**
   - Follows existing patterns in the codebase?
   - Consistent with CLAUDE.md / project standards?
   - DRY — no duplicated logic?

3. **Categorize issues:**

   - **Critical** — Must fix before proceeding (bugs, security holes, data loss risk)
   - **Important** — Should fix before merging (maintainability, robustness gaps)
   - **Minor** — Nice to fix but not blocking (style, naming nits)
   - **Suggestion** — Optional improvements for future consideration

4. **Verdict:**

   - ✅ **APPROVED** — no Critical or Important issues remaining
   - ⚠️ **APPROVED WITH NOTES** — no Critical, minor Important items noted
   - ❌ **CHANGES REQUESTED** — Critical or Important issues must be fixed

**Output Format:**

```
## Code Quality Review: Task {TASK_NUMBER}

### Strengths
- [What was done well]
- [Good patterns observed]

### Issues

**Critical:**
- [Issue] — [file:line] — [fix recommendation]

**Important:**
- [Issue] — [file:line] — [fix recommendation]

**Minor:**
- [Issue] — [file:line] — [fix recommendation]

**Suggestions:**
- [Improvement idea]

### Verdict: ✅ APPROVED / ⚠️ APPROVED WITH NOTES / ❌ CHANGES REQUESTED

### Summary
[1-2 sentence overall assessment]
```

---

## Controller Responsibilities

After receiving the code quality review:

- **If ✅ APPROVED or ⚠️ APPROVED WITH NOTES:** Mark task complete, proceed to next task.
- **If ❌ CHANGES REQUESTED:** Send Critical/Important issues back to the implementer subagent to fix, then re-run this review. Do NOT move to the next task.

## Relationship to Spec Review

```
Spec Compliance (gate 1)     Code Quality (gate 2)
────────────────────────     ─────────────────────
Does it do the right thing?  Does it do it well?
Binary: compliant or not     Graduated: critical → minor
Must pass first              Only runs after gate 1 passes
```

Both gates must pass before a task is considered complete.
