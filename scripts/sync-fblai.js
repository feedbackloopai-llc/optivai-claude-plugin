#!/usr/bin/env node

/**
 * FBLAI Content Sync Script
 *
 * Synchronizes instructions, business artifacts, and standards from the
 * feedbackloopai-llc/fblai-ade-claude-vsce private repository.
 *
 * Usage: node scripts/sync-fblai.js [--force]
 *
 * Environment:
 *   GITHUB_TOKEN - GitHub Personal Access Token with 'repo' scope
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');

// Configuration
const REPO_OWNER = 'feedbackloopai-llc';
const REPO_NAME = 'fblai-ade-claude-vsce';
const REPO_BRANCH = 'main';
const OUTPUT_DIR = path.join(__dirname, '../instructions');
const MANIFEST_CACHE = path.join(__dirname, '../.fblai-manifest-cache.json');

// Content paths to sync (relative to repo root)
const SYNC_PATHS = [
  'instructions/business-artifact-instructions',
  'instructions/global',
  'instructions/style-guides'
];

// Colors for terminal output
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m'
};

/**
 * Get GitHub token from environment or file
 */
function getGithubToken() {
  // Check environment variable
  if (process.env.GITHUB_TOKEN) {
    return process.env.GITHUB_TOKEN;
  }

  // Check .optivai-github-token file
  const tokenFile = path.join(__dirname, '../.optivai-github-token');
  if (fs.existsSync(tokenFile)) {
    return fs.readFileSync(tokenFile, 'utf-8').trim();
  }

  return null;
}

/**
 * Make GitHub API request
 */
function githubRequest(apiPath, token) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'api.github.com',
      path: apiPath,
      method: 'GET',
      headers: {
        'User-Agent': 'optivai-claude-plugin-sync',
        'Accept': 'application/vnd.github.v3+json',
        'Authorization': `Bearer ${token}`
      }
    };

    https.get(options, (res) => {
      let data = '';

      res.on('data', (chunk) => {
        data += chunk;
      });

      res.on('end', () => {
        if (res.statusCode === 200) {
          resolve(JSON.parse(data));
        } else {
          reject(new Error(`GitHub API error: ${res.statusCode} - ${data}`));
        }
      });
    }).on('error', (err) => {
      reject(err);
    });
  });
}

/**
 * Download file content from GitHub
 */
function downloadFile(filePath, token) {
  const apiPath = `/repos/${REPO_OWNER}/${REPO_NAME}/contents/${filePath}?ref=${REPO_BRANCH}`;
  return githubRequest(apiPath, token);
}

/**
 * List directory contents from GitHub
 */
function listDirectory(dirPath, token) {
  const apiPath = `/repos/${REPO_OWNER}/${REPO_NAME}/contents/${dirPath}?ref=${REPO_BRANCH}`;
  return githubRequest(apiPath, token);
}

/**
 * Calculate SHA-256 checksum of content
 */
function calculateChecksum(content) {
  return crypto.createHash('sha256').update(content).digest('hex');
}

/**
 * Recursively download directory contents
 */
async function downloadDirectory(repoPath, localPath, token, stats) {
  try {
    const contents = await listDirectory(repoPath, token);

    // Create local directory if it doesn't exist
    if (!fs.existsSync(localPath)) {
      fs.mkdirSync(localPath, { recursive: true });
    }

    for (const item of contents) {
      if (item.type === 'file') {
        // Download file
        const fileData = await downloadFile(item.path, token);
        const content = Buffer.from(fileData.content, 'base64').toString('utf-8');

        // Calculate checksum
        const checksum = calculateChecksum(content);

        // Write file
        const outputPath = path.join(localPath, item.name);
        fs.writeFileSync(outputPath, content, 'utf-8');

        stats.downloaded++;
        console.log(`  ${colors.green}✓${colors.reset} ${item.name}`);
      } else if (item.type === 'dir') {
        // Recursively download subdirectory
        const subRepoPath = item.path;
        const subLocalPath = path.join(localPath, item.name);
        await downloadDirectory(subRepoPath, subLocalPath, token, stats);
      }
    }
  } catch (error) {
    console.error(`  ${colors.red}✗${colors.reset} Failed to download ${repoPath}: ${error.message}`);
    stats.failed++;
  }
}

/**
 * Load cached manifest
 */
function loadManifestCache() {
  if (fs.existsSync(MANIFEST_CACHE)) {
    try {
      return JSON.parse(fs.readFileSync(MANIFEST_CACHE, 'utf-8'));
    } catch (error) {
      console.warn(`${colors.yellow}⚠${colors.reset} Failed to load manifest cache: ${error.message}`);
    }
  }
  return null;
}

/**
 * Save manifest cache
 */
function saveManifestCache(manifest) {
  try {
    fs.writeFileSync(MANIFEST_CACHE, JSON.stringify(manifest, null, 2), 'utf-8');
  } catch (error) {
    console.warn(`${colors.yellow}⚠${colors.reset} Failed to save manifest cache: ${error.message}`);
  }
}

/**
 * Main sync function
 */
async function sync() {
  console.log(`${colors.bright}${colors.cyan}🔄 Syncing FBLAI Content${colors.reset}`);
  console.log('========================\n');

  // Get GitHub token
  const token = getGithubToken();
  if (!token) {
    console.error(`${colors.red}✗ GitHub token not found${colors.reset}`);
    console.error('\nPlease set GITHUB_TOKEN environment variable or create .optivai-github-token file');
    console.error('Token must have "repo" scope for private repository access\n');
    process.exit(1);
  }

  // Verify token works
  console.log(`${colors.blue}🔐 Verifying GitHub authentication...${colors.reset}`);
  try {
    await githubRequest(`/repos/${REPO_OWNER}/${REPO_NAME}`, token);
    console.log(`${colors.green}✓${colors.reset} Authentication successful\n`);
  } catch (error) {
    console.error(`${colors.red}✗ Authentication failed: ${error.message}${colors.reset}\n`);
    process.exit(1);
  }

  // Download statistics
  const stats = {
    downloaded: 0,
    skipped: 0,
    failed: 0
  };

  // Download each path
  console.log(`${colors.blue}📥 Downloading content...${colors.reset}\n`);

  for (const syncPath of SYNC_PATHS) {
    const pathName = syncPath.split('/').pop();
    const localPath = path.join(OUTPUT_DIR, pathName);

    console.log(`${colors.cyan}${pathName}/${colors.reset}`);
    await downloadDirectory(syncPath, localPath, token, stats);
  }

  // Save sync timestamp
  const manifest = {
    version: '1.1.0',
    lastSync: new Date().toISOString(),
    repository: `${REPO_OWNER}/${REPO_NAME}`,
    branch: REPO_BRANCH,
    stats
  };
  saveManifestCache(manifest);

  // Print summary
  console.log('\n========================');
  console.log(`${colors.bright}${colors.green}✓ Sync complete!${colors.reset}`);
  console.log(`  Downloaded: ${stats.downloaded} files`);
  console.log(`  Skipped: ${stats.skipped} files`);
  console.log(`  Failed: ${stats.failed} files`);

  if (stats.failed > 0) {
    console.log(`\n${colors.yellow}⚠ Some files failed to download. Check errors above.${colors.reset}`);
  }

  console.log(`\n${colors.cyan}📁 Instructions available in: ${OUTPUT_DIR}${colors.reset}`);
  console.log(`${colors.cyan}💾 Manifest cached in: ${MANIFEST_CACHE}${colors.reset}\n`);
}

// Run sync
sync().catch(error => {
  console.error(`\n${colors.red}✗ Sync failed: ${error.message}${colors.reset}\n`);
  process.exit(1);
});
