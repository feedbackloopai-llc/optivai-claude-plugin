#!/usr/bin/env node

/**
 * Update plugin.json Manifest
 *
 * Scans agents/ and commands/ directories and updates .claude-plugin/plugin.json
 * with all available agents and commands.
 *
 * Usage: node scripts/update-manifest.js
 */

const fs = require('fs');
const path = require('path');

const PLUGIN_JSON_PATH = path.join(__dirname, '../.claude-plugin/plugin.json');
const AGENTS_DIR = path.join(__dirname, '../agents');
const COMMANDS_DIR = path.join(__dirname, '../commands');

/**
 * Get all markdown files from a directory
 */
function getMarkdownFiles(dir) {
  if (!fs.existsSync(dir)) {
    return [];
  }
  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.md'))
    .map(f => path.basename(f, '.md'))
    .sort();
}

/**
 * Update plugin.json with new agents and commands
 */
function main() {
  console.log('📝 Updating plugin.json manifest');
  console.log('=================================\n');

  // Read current plugin.json
  const pluginJson = JSON.parse(fs.readFileSync(PLUGIN_JSON_PATH, 'utf-8'));

  // Scan for agents
  const agents = getMarkdownFiles(AGENTS_DIR);
  console.log(`🤖 Found ${agents.length} agents:`);
  agents.forEach(agent => console.log(`   - ${agent}`));

  // Scan for commands
  const commands = getMarkdownFiles(COMMANDS_DIR);
  console.log(`\n⚡ Found ${commands.length} commands:`);
  commands.forEach(cmd => console.log(`   - ${cmd}`));

  // Update plugin.json
  pluginJson.agents = agents;
  pluginJson.commands = commands;

  // Update description and component counts
  pluginJson.description = `Comprehensive AI agent toolkit with ${agents.length} specialized agents, ${commands.length}+ workflow commands, and enterprise business artifact generation`;
  pluginJson.components.agents.description = `${agents.length} specialized agents for various development tasks`;
  pluginJson.components.commands.description = `${commands.length} workflow automation commands`;

  // Update version (minor bump for new features)
  const currentVersion = pluginJson.version.split('.');
  const newVersion = `${currentVersion[0]}.${parseInt(currentVersion[1]) + 1}.0`;
  pluginJson.version = newVersion;

  // Write updated plugin.json
  fs.writeFileSync(
    PLUGIN_JSON_PATH,
    JSON.stringify(pluginJson, null, 2) + '\n',
    'utf-8'
  );

  console.log('\n=================================');
  console.log(`✅ Updated plugin.json`);
  console.log(`   Version: ${pluginJson.version}`);
  console.log(`   Agents: ${agents.length}`);
  console.log(`   Commands: ${commands.length}`);
  console.log('\n✨ Manifest update complete!');
}

// Run update
main();
