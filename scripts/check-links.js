#!/usr/bin/env node

/**
 * Documentation Link Checker
 *
 * Verifies all markdown links in documentation files
 */

const fs = require('fs');

function checkLinks(filePath) {
  const content = fs.readFileSync(filePath, 'utf-8');
  const links = [...content.matchAll(/\[([^\]]+)\]\(([^)]+)\)/g)];

  const issues = [];

  links.forEach(match => {
    const linkText = match[1];
    const linkPath = match[2];

    // Skip external URLs
    if (linkPath.startsWith('http://') || linkPath.startsWith('https://')) {
      return;
    }

    // Skip anchors within same document
    if (linkPath.startsWith('#')) {
      return;
    }

    // Check if local file exists
    const fullPath = linkPath.split('#')[0]; // Remove anchor
    if (!fs.existsSync(fullPath)) {
      issues.push({ text: linkText, path: linkPath });
    }
  });

  return issues;
}

const files = [
  'README.md',
  'AGENT-CATALOG.md',
  'INTEGRATION-COMPLETE.md',
  'CHANGELOG.md',
  'RELEASE-NOTES.md'
];

console.log('Checking documentation links...\n');

let totalIssues = 0;

files.forEach(file => {
  if (fs.existsSync(file)) {
    const issues = checkLinks(file);
    if (issues.length > 0) {
      console.log(file, '- ✗', issues.length, 'broken links');
      issues.forEach(i => console.log('  -', i.text, '->', i.path));
      totalIssues += issues.length;
    } else {
      console.log(file, '- ✓ All links valid');
    }
  }
});

console.log('\n========================');
if (totalIssues === 0) {
  console.log('✓ All documentation links valid');
} else {
  console.log('✗', totalIssues, 'broken links found');
}
