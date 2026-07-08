---
description: Independent adversarial refuter - break a claim with a fresh, isolated mind (free local model, cloud fallback).
---

# /refute - independent adversarial review

The anti-persuasion-bombing structural check. A single model cannot reliably check itself,
so run a FRESH mind with ISOLATED inputs (only the claim, never your own reasoning or this
conversation's history) whose one job is to REFUTE, not bless. This is the enforcement the
Truth-Over-Engagement contract cannot deliver on its own.

CLAIM TO REFUTE (if `$ARGUMENTS` is empty, use the most recent substantive recommendation or
decision in this conversation, quoted verbatim):

$ARGUMENTS

Do exactly this:

1. **Free local pass first.** Run:
   ```
   python3 ~/.claude/refute.py --claim "<the claim, quoted>"
   ```
   It routes to a local model (no token cost, no egress) with isolated inputs and returns
   `{verdict, strongestCounterCase, flaws, confidenceAdjustment}`. Add `--context "..."` if the
   claim came with grounding, and `--confidence "..."` if it was asserted with a stated confidence.

2. **Escalate when it matters.** If `refute.py` exits 2 (no local model reachable), OR the claim
   is high-stakes (a destructive/irreversible action, a customer-facing commitment, a security or
   money decision), ALSO dispatch a FRESH general-purpose subagent via the Task tool with ONLY the
   isolated claim and this instruction:
   > "Your one job is to REFUTE this claim. Construct the strongest counter-case even if it looks
   > correct. Return `{verdict: holds|gap|broken, strongestCounterCase (REQUIRED even on holds -
   > a bare 'looks good' is a failure), flaws:[{claim,severity}], confidenceAdjustment}`."
   Do NOT pass the subagent your conversation history - isolation is the point.

3. **Re-examine, do not defend.** Present the refutation plainly. If the verdict is `gap`/`broken`
   or the confidence adjustment is negative, RE-EXAMINE the original claim against the counter-case
   (contract clause 7): report what changed and what held, with the re-derivation. Do not marshal an
   avalanche of new argument to defend the first answer - that reflex is the exact behavior this
   check exists to stop.
