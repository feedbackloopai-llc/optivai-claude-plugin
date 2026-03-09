# Code Reviewer Dispatch Template

Use this template when dispatching a code review subagent via the Task tool
after completing a feature, task, or batch of work.

## Template

Fill in the `{PLACEHOLDERS}` and dispatch via Task tool with the `code-quality-reviewer` agent type.

---

**Role:** Code Quality Reviewer

**What Was Implemented:**
{WHAT_WAS_IMPLEMENTED}

**Plan / Requirements:**
{PLAN_OR_REQUIREMENTS}

**Git Diff Range:**
Base: `{BASE_SHA}`
Head: `{HEAD_SHA}`

To see the diff: `git diff {BASE_SHA}..{HEAD_SHA}`

**Description:**
{DESCRIPTION}

**Review Focus:**
Review the diff for:
1. **Correctness** — bugs, edge cases, error handling
2. **Security** — injection, credential exposure, input validation
3. **Performance** — inefficiencies, N+1 patterns, resource leaks
4. **Readability** — naming, function size, comments where needed
5. **Testing** — coverage, meaningful assertions, edge case tests
6. **Conventions** — project patterns, CLAUDE.md standards, DRY

**Output Format:**

```
## Code Review: {DESCRIPTION}

### Strengths
- [What was done well]

### Issues

**Critical (must fix):**
- [Issue] — [file:line] — [recommendation]

**Important (should fix):**
- [Issue] — [file:line] — [recommendation]

**Minor (nice to fix):**
- [Issue] — [recommendation]

### Assessment
[Overall readiness: Ready to merge / Needs fixes / Major rework needed]
```

---

## Quick Reference: Getting Git SHAs

```bash
# SHA before your changes (common ancestors)
BASE_SHA=$(git merge-base HEAD main)      # vs main branch
BASE_SHA=$(git rev-parse HEAD~5)          # vs 5 commits ago
BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')  # vs specific commit

# Current SHA
HEAD_SHA=$(git rev-parse HEAD)

# Preview what reviewer will see
git diff $BASE_SHA..$HEAD_SHA --stat
```

## When to Use This vs. Subagent-Driven Reviews

| Scenario | Use This | Use Subagent-Driven |
|----------|----------|-------------------|
| Ad-hoc feature complete | ✅ | |
| Pre-merge review | ✅ | |
| Within plan execution | | ✅ (automatic per task) |
| When stuck, need perspective | ✅ | |
| Before refactoring | ✅ | |
