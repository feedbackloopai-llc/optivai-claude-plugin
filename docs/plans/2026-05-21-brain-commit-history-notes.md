# Brain v0.2.0-neurosymbolic — Commit History Notes

This is a small ledger of historical clarifications about specific commits on the `brain-v0.2.0-neurosymbolic` branch. Filed as part of follow-up work after the W1-R0 + W2-R0 reviews.

## `2afff31` — `feat(brain-W1-S1): PROV-DM schema migration on brain.thoughts (gz-ej5il)`

**Doc note (gz-xbq0q):** the commit message claims PROV-DM-only scope, but the commit additionally bundled 6 lines of pre-existing Sentinel-related additions to `scripts/open_brain.py` that were in the working tree at branch-cut time. Specifically:

- `METADATA_EXTRACTION_PROMPT` `type` enum extended with `sentinel_event|sentinel_relevant`
- `Types:` bullet list in the same prompt extended with the two corresponding bullets
- `--type` argparse help string extended with the two new values

These additions were not part of the PROV-DM design; they were carried forward on the branch per the user's directive ("branch from current state, carry changes") at session start, then bundled into the first feature commit because of how `git add scripts/open_brain.py` works (it adds ALL working-tree changes to that file, not just the lines you edited).

**Functional impact:** none. The Sentinel type additions are orthogonal to the PROV-DM work and pass all tests.

**Process lesson:** when branching with a dirty working tree, prefer `git stash` + `git checkout -b branch` + `git stash pop` to isolate pre-existing changes from new feature commits. Or use `git add -p` for fine-grained selection rather than file-level `git add`.

---

## `5280779` — `feat(brain-W1-S13 + R0.2): Hebbian search integration + SQL aggregate`

**Field-name deviation note:** the bead spec (in `docs/plans/2026-05-21-brain-neurosymbolic-port.md`) referenced `final_score` and `vec_similarity` as the field names on search result rows. The actual `search()` function uppercases all result keys at line 2077 (`d = {k.upper(): v for k, v in d.items()}`). The implementation therefore reads/writes `SIMILARITY`, `HYBRID_SCORE`, `EFFECTIVE_WEIGHT`, and `PROMOTION_BOOST` (uppercase). The implementer adapted tests with a defensive `_result_tid` helper that reads both casings, but the canonical form on the wire is uppercase.

This is captured here for any future bead that references the search-result schema — use uppercase field names when reasoning about the live data.
