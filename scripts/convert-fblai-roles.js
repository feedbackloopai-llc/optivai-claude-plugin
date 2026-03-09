#!/usr/bin/env node

/**
 * FBLAI to OptivAI Agent Conversion Script
 *
 * Converts FBLAI AI roles to OptivAI agent format with YAML frontmatter.
 *
 * Usage: node scripts/convert-fblai-roles.js <fblai-ade-path>
 */

const fs = require('fs');
const path = require('path');

// Configuration
const FBLAI_ROLES_DIR = process.argv[2]
  ? path.join(process.argv[2], 'instructions', 'ai-roles')
  : path.join(__dirname, '../../fblai-ade-claude-vsce/fblai-ade-claude-vsce/instructions/ai-roles');

const FBLAI_AGENTS_DIR = process.argv[2]
  ? path.join(process.argv[2], 'instructions', 'agents')
  : path.join(__dirname, '../../fblai-ade-claude-vsce/fblai-ade-claude-vsce/instructions/agents');

const OUTPUT_DIR = path.join(__dirname, '../agents');

// Model and color assignment logic
const MODEL_ASSIGNMENTS = {
  // Data Quality roles - opus (complex analysis)
  'data-quality-analyst': { model: 'opus', color: 'blue' },
  'data-quality-manager': { model: 'opus', color: 'blue' },
  'data-steward': { model: 'sonnet', color: 'cyan' },
  'data-governance-lead': { model: 'opus', color: 'purple' },
  'data-engineer': { model: 'sonnet', color: 'green' },
  'data-architect': { model: 'opus', color: 'purple' },
  'database-administrator': { model: 'sonnet', color: 'green' },
  'data-scientist': { model: 'opus', color: 'purple' },

  // Business roles - sonnet (balanced)
  'business-analyst': { model: 'sonnet', color: 'cyan' },
  'subject-matter-expert': { model: 'sonnet', color: 'yellow' },
  'product-manager': { model: 'sonnet', color: 'cyan' },
  'product-owner': { model: 'sonnet', color: 'cyan' },
  'program-manager': { model: 'sonnet', color: 'blue' },

  // Compliance/Legal - opus (critical)
  'compliance-officer': { model: 'opus', color: 'red' },
  'immigration-law-sme': { model: 'opus', color: 'red' },

  // Change management - sonnet
  'change-management-specialist': { model: 'sonnet', color: 'yellow' },

  // Technical roles - varies
  'senior-engineer': { model: 'opus', color: 'purple' },
  'solution-architect': { model: 'opus', color: 'purple' },
  'machine-learning-engineer': { model: 'opus', color: 'purple' },

  // Strategic/Market - opus
  'market-analysis-mgr': { model: 'opus', color: 'blue' },
  'strategic-planning-manager': { model: 'opus', color: 'purple' },
  'user-research-expert': { model: 'sonnet', color: 'cyan' },
  'ux-ui-design-manager': { model: 'sonnet', color: 'cyan' },
  'financial-analyst': { model: 'opus', color: 'blue' },

  // GenSI agents - opus (orchestration)
  'gensi-phase0-executor': { model: 'opus', color: 'magenta' },
  'gensi-phase1-initiative-planner': { model: 'opus', color: 'magenta' },
  'gensi-phase1-initiative-executor': { model: 'opus', color: 'magenta' },
  'gensi-phase2-initiative-worker': { model: 'opus', color: 'magenta' },
  'gensi-phase3-initiative-worker': { model: 'opus', color: 'magenta' },
  'gensi-phase4-initiative-worker': { model: 'opus', color: 'magenta' },
  'gensi-phase5-initiative-worker': { model: 'opus', color: 'magenta' },
  'gensi-phase6-initiative-worker': { model: 'opus', color: 'magenta' }
};

/**
 * Extract role name from filename
 */
function getRoleName(filename) {
  return path.basename(filename, '.md');
}

/**
 * Convert filename to display name
 * Example: data-quality-analyst.md -> Data Quality Analyst
 */
function toDisplayName(filename) {
  const roleName = getRoleName(filename);
  return roleName
    .split('-')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Extract short description from FBLAI role content
 */
function extractDescription(content, roleName) {
  // Try to extract from "You are now operating as..." section
  const roleDefMatch = content.match(/You are now operating as a?\*?\*([^*]+)\*?\*\.?\s+Your expertise includes:/i);
  if (roleDefMatch) {
    const role = roleDefMatch[1].trim();
    return `Expert ${role} for specialized domain expertise.\n\nUse when: Need ${role.toLowerCase()} expertise for analysis, planning, or execution.`;
  }

  // Fallback: Generic description
  const displayName = toDisplayName(roleName);
  return `Expert ${displayName} for specialized domain expertise.\n\nUse when: Need ${displayName.toLowerCase()} expertise for analysis, planning, or execution.`;
}

/**
 * Convert FBLAI role to OptivAI agent format
 */
function convertRole(fblaiPath) {
  const roleName = getRoleName(fblaiPath);
  const content = fs.readFileSync(fblaiPath, 'utf-8');

  // Get model and color assignment
  const assignment = MODEL_ASSIGNMENTS[roleName] || { model: 'sonnet', color: 'blue' };

  // Extract description
  const description = extractDescription(content, roleName);

  // Generate YAML frontmatter
  const frontmatter = `---
name: ${roleName}
description: ${description}
model: ${assignment.model}
color: ${assignment.color}
---

`;

  // Combine frontmatter with original content
  return frontmatter + content;
}

/**
 * Main conversion function
 */
function main() {
  console.log('🔄 FBLAI to OptivAI Agent Conversion');
  console.log('=====================================\n');

  // Ensure output directory exists
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  let convertedCount = 0;
  let skippedCount = 0;

  // Process AI roles
  console.log(`📂 Processing AI roles from: ${FBLAI_ROLES_DIR}`);
  if (fs.existsSync(FBLAI_ROLES_DIR)) {
    const roleFiles = fs.readdirSync(FBLAI_ROLES_DIR)
      .filter(f => f.endsWith('.md'));

    for (const roleFile of roleFiles) {
      const roleName = getRoleName(roleFile);
      const inputPath = path.join(FBLAI_ROLES_DIR, roleFile);
      const outputPath = path.join(OUTPUT_DIR, roleFile);

      // Skip if already exists in OptivAI (check for conflicts)
      if (fs.existsSync(outputPath)) {
        console.log(`⚠️  Skipping ${roleName} (already exists - manual merge required)`);
        skippedCount++;
        continue;
      }

      try {
        const converted = convertRole(inputPath);
        fs.writeFileSync(outputPath, converted, 'utf-8');
        console.log(`✅ Converted ${roleName} (${MODEL_ASSIGNMENTS[roleName]?.model || 'sonnet'})`);
        convertedCount++;
      } catch (error) {
        console.error(`❌ Failed to convert ${roleName}:`, error.message);
      }
    }
  }

  // Process GenSI agents
  console.log(`\n📂 Processing GenSI agents from: ${FBLAI_AGENTS_DIR}`);
  if (fs.existsSync(FBLAI_AGENTS_DIR)) {
    const agentFiles = fs.readdirSync(FBLAI_AGENTS_DIR)
      .filter(f => f.endsWith('.md'));

    for (const agentFile of agentFiles) {
      const agentName = getRoleName(agentFile);
      const inputPath = path.join(FBLAI_AGENTS_DIR, agentFile);
      const outputPath = path.join(OUTPUT_DIR, agentFile);

      if (fs.existsSync(outputPath)) {
        console.log(`⚠️  Skipping ${agentName} (already exists)`);
        skippedCount++;
        continue;
      }

      try {
        const converted = convertRole(inputPath);
        fs.writeFileSync(outputPath, converted, 'utf-8');
        console.log(`✅ Converted ${agentName} (${MODEL_ASSIGNMENTS[agentName]?.model || 'opus'})`);
        convertedCount++;
      } catch (error) {
        console.error(`❌ Failed to convert ${agentName}:`, error.message);
      }
    }
  }

  console.log('\n=====================================');
  console.log(`✅ Converted: ${convertedCount} agents`);
  console.log(`⚠️  Skipped: ${skippedCount} agents`);
  console.log(`📁 Output directory: ${OUTPUT_DIR}`);
  console.log('\n✨ Conversion complete! Run update-manifest.js to update plugin.json');
}

// Run conversion
main();
