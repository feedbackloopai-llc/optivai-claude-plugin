#!/usr/bin/env node

/**
 * Test Agent Validation Script
 *
 * Validates agent YAML frontmatter and structure
 */

const fs = require('fs');
const path = require('path');

function testAgent(agentFile) {
  const content = fs.readFileSync(agentFile, 'utf-8');

  // More flexible regex that handles different line endings
  const frontmatterMatch = content.match(/^---\r?\n([\s\S]+?)\r?\n---/);

  if (!frontmatterMatch) {
    throw new Error('No frontmatter found');
  }

  const yaml = frontmatterMatch[1];

  // Check for required fields
  if (!yaml.includes('name:')) throw new Error('Missing name');
  if (!yaml.includes('description:')) throw new Error('Missing description');
  if (!yaml.includes('model:')) throw new Error('Missing model');

  // Check for duplicate frontmatter at start of file
  // Split on first closing --- and check if there's another frontmatter block
  const afterFirstBlock = content.substring(frontmatterMatch.index + frontmatterMatch[0].length);
  const hasSecondFrontmatter = /^\s*---\r?\n[\s\S]+?\r?\n---/.test(afterFirstBlock.substring(0, 500));

  if (hasSecondFrontmatter) {
    throw new Error('Duplicate frontmatter detected');
  }

  return true;
}

// Test all agents from manifest
const manifest = JSON.parse(fs.readFileSync('.claude-plugin/plugin.json', 'utf-8'));

console.log('Testing all agents YAML frontmatter...\n');

let passed = 0;
let failed = 0;
const failures = [];

manifest.agents.forEach(agentName => {
  const agentPath = path.join('agents', agentName + '.md');
  try {
    testAgent(agentPath);
    passed++;
  } catch (error) {
    console.log('✗', agentName, '-', error.message);
    failed++;
    failures.push({ agent: agentName, error: error.message });
  }
});

console.log('\n========================');
console.log('Total:', manifest.agents.length);
console.log('Passed:', passed);
console.log('Failed:', failed);

if (failed > 0) {
  console.log('\n❌ Failed agents:');
  failures.forEach(f => console.log('  -', f.agent, ':', f.error));
  process.exit(1);
}

console.log('\n✅ All', passed, 'agents valid');
