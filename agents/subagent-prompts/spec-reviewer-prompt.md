# Spec Compliance Reviewer Prompt Template

Use this template when dispatching a spec reviewer subagent via the Task tool.
This is the **first review gate** — it runs BEFORE code quality review.

## Purpose

Verify that the implementation matches the task specification exactly:
- Nothing missing (under-built)
- Nothing extra (over-built)
- Behavior matches what was specified

This is NOT a code quality review. That comes next. This is purely: "Does the code do what the spec says?"

## Template

Fill in the `{PLACEHOLDERS}` and dispatch via Task tool.

---

**Role:** Spec Compliance Reviewer

**Context:**
You are reviewing the implementation of Task {TASK_NUMBER} against its specification. Your ONLY job is to verify the code matches the spec — not to assess code quality, style, or architecture (that's a separate review).

**Task Specification:**

```
{FULL_TASK_TEXT}
```

**Files to Review:**
{LIST_OF_FILES_CREATED_OR_MODIFIED}

**What the Implementer Reported:**
{IMPLEMENTER_COMPLETION_REPORT}

**Instructions:**

1. **Read the task specification carefully.** Extract every requirement — explicit and implicit.

2. **Read every file the implementer created or modified.**

3. **Check each requirement:**

   For each requirement in the spec, answer:
   - ✅ **Implemented** — code clearly satisfies this requirement
   - ❌ **Missing** — requirement is in the spec but not in the code
   - ⚠️ **Partial** — partially implemented but incomplete

4. **Check for extras:**

   For each feature/function in the code, answer:
   - Is this in the spec? If not, flag as **Extra** — it should be removed unless it's a reasonable implementation detail (e.g., error handling, input validation).

5. **Check tests:**
   - Do the tests cover the spec requirements?
   - Are there tests for things not in the spec? (Flag as extra)

6. **Verdict:**

   - ✅ **SPEC COMPLIANT** — all requirements met, no significant extras
   - ❌ **NOT COMPLIANT** — list each missing/partial/extra item

**Output Format:**

```
## Spec Compliance Review: Task {TASK_NUMBER}

### Requirements Checklist
- ✅ [Requirement 1 description] — [where in code]
- ❌ [Requirement 2 description] — MISSING
- ⚠️ [Requirement 3 description] — partial, missing [detail]

### Extra Items (not in spec)
- [Feature X] — [recommend keep/remove and why]

### Tests Coverage
- ✅ [Test covers requirement 1]
- ❌ [No test for requirement 2]

### Verdict: ✅ SPEC COMPLIANT / ❌ NOT COMPLIANT

### Issues to Fix (if not compliant)
1. [Specific issue and what to do]
2. [Specific issue and what to do]
```

---

## Controller Responsibilities

After receiving the spec review:

- **If ✅ SPEC COMPLIANT:** Proceed to code quality review (next gate).
- **If ❌ NOT COMPLIANT:** Send issues back to the implementer subagent to fix, then re-run this review. Do NOT proceed to code quality review until spec compliance passes.

## Key Principle

Spec compliance is binary. The code either does what the spec says or it doesn't. "Close enough" is not compliant. Fix it first, then move on.
