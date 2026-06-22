# Git hooks for optivai-pi-plugin

## Pre-commit: macOS sync-conflict guard

`pre-commit` blocks commits that include macOS Files-app sync-conflict
duplicates — files matching the pattern `<name> <space><digit>` (e.g.
`README 2.md`, `tools 3.json`) or `.sync-conflict-<timestamp>`.

These files appear when iCloud / Files-app resolves a sync collision; if
they slip into the repo they pollute history, confuse imports, and cause
test failures (the same pattern is documented in the parent CLAUDE.md as
P16-S4).

### Install (one-time per clone)

```bash
cp scripts/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

Or in one shot from the repo root:
```bash
install -m 0755 scripts/git-hooks/pre-commit .git/hooks/pre-commit
```

### Override (emergencies only)

```bash
git commit --no-verify
```

If you find yourself reaching for `--no-verify` more than once, the
sync-conflict pattern is the right thing to fix — not bypass.
