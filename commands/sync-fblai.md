# Sync FBLAI Content

Synchronize FBLAI instructions, business artifacts, and standards from the private GitHub repository.

## Execution

When the user invokes `/sync-fblai`, execute:

```bash
node scripts/sync-fblai.js
```

Monitor the output and display progress/results to the user.

## What Gets Synced

Downloads from `feedbackloopai-llc/fblai-ade-claude-vsce`:
- **business-artifact-instructions/** - Business artifact templates (SOW, strategic plans, market research)
- **global/** - General instructions, coding standards, security practices
- **style-guides/** - Documentation style guides and templates

Content saved to `instructions/` directory in plugin root.

## Prerequisites

- GitHub Personal Access Token with `repo` scope
- Access to private repository `feedbackloopai-llc/fblai-ade-claude-vsce`

## Setting Up GitHub Token

**Option 1: Environment Variable**
```bash
export GITHUB_TOKEN=ghp_your_token_here
/sync-fblai
```

**Option 2: Token File** (Recommended)
```bash
echo "ghp_your_token_here" > .optivai-github-token
chmod 600 .optivai-github-token
/sync-fblai
```

The `.optivai-github-token` file is gitignored for security.

## Expected Output

```
🔄 Syncing FBLAI Content
========================

🔐 Verifying GitHub authentication...
✓ Authentication successful

📥 Downloading content...

business-artifact-instructions/
  ✓ sow-template.md
  ✓ strategic-planning-guide.md
  ... (more files)

global/
  ✓ general-instructions.md
  ✓ coding-standards.md
  ... (more files)

style-guides/
  ✓ documentation-guidelines.md
  ... (more files)

========================
✓ Sync complete!
  Downloaded: 37 files
  Skipped: 0 files
  Failed: 0 files

📁 Instructions available in: ./instructions/
💾 Manifest cached in: ./.fblai-manifest-cache.json
```

## Directory Structure Created

```
optivai-claude-plugin/
├── instructions/
│   ├── business-artifact-instructions/
│   ├── global/
│   └── style-guides/
└── .fblai-manifest-cache.json
```

## Error Handling

If sync fails, check:

**Authentication Failed**
- Verify GitHub token has `repo` scope at https://github.com/settings/tokens
- Ensure token has access to private repository
- Check if token expired

**Network Errors**
- Retry sync operation
- Check internet connection
- Verify GitHub API accessibility

**Permission Errors**
- Ensure write permissions to plugin directory
- Check `.optivai-github-token` file permissions (should be 600)

## Security Notes

- GitHub tokens are sensitive - never commit to version control
- Store in environment variable or secure file (mode 600)
- Rotate tokens regularly

## Implementation

The sync script (`scripts/sync-fblai.js`):
1. Uses GitHub REST API for file downloads
2. Downloads content recursively for each sync path
3. Saves files maintaining original structure
4. Caches manifest for tracking

## Related Commands

- `/prime-agent` - Load project context with synced instructions
- `/create-prp` - Generate requirements using synced templates
- `/gensi` - Strategic planning with synced frameworks
