# JIRA - Create and Manage Tickets

**Purpose**: Create, update, and manage JIRA tickets directly from Claude Code
**Usage**: `/jira <action> [args]`

## Actions

| Action | Usage | Description |
|--------|-------|-------------|
| `create` | `/jira create "Title" [options]` | Create new ticket |
| `assign` | `/jira assign DA-123 username` | Assign ticket |
| `comment` | `/jira comment DA-123 "text"` | Add comment |
| `transition` | `/jira transition DA-123 "Done"` | Change status |
| `search` | `/jira search "JQL query"` | Search tickets |
| `view` | `/jira view DA-123` | View ticket details |
| `test` | `/jira test` | Test connection |

## Quick Examples

```bash
# Create a simple task
/jira create "Update documentation"

# Create with assignee
/jira create "Fix login bug" --assignee chandler

# Create with multiple watchers
/jira create "Review PR" --watchers marshall,chris,yemi

# Search open tickets
/jira search "project = DA AND status != Done"
```

## Arguments: $ARGUMENTS

## Execution

Execute the requested JIRA operation using the Python client library.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / '.claude/lib'))
from jira_client import JiraClient

jira = JiraClient()

# Parse the command from $ARGUMENTS
args = """$ARGUMENTS""".strip()

if not args or args == 'test':
    # Test connection
    me = jira.get_myself()
    print(f"Connected to JIRA as: {me.get('displayName')}")
    print(f"Default Project: {jira.config['defaults']['project_key']} ({jira.config['defaults']['project_name']})")
    print(f"Instance: {jira.config['instance_url']}")

elif args.startswith('create '):
    # Parse create command
    import re
    import shlex

    # Extract title (quoted string after 'create')
    title_match = re.search(r'create\s+"([^"]+)"', args)
    if not title_match:
        title_match = re.search(r"create\s+'([^']+)'", args)
    if not title_match:
        # Try unquoted
        parts = args.split(' ', 2)
        title = parts[1] if len(parts) > 1 else "Untitled"
    else:
        title = title_match.group(1)

    # Extract options
    assignee = None
    watchers = []
    description = None
    priority = None
    labels = []

    if '--assignee' in args:
        match = re.search(r'--assignee\s+(\S+)', args)
        if match:
            assignee = match.group(1)

    if '--watchers' in args:
        match = re.search(r'--watchers\s+(\S+)', args)
        if match:
            watchers = match.group(1).split(',')

    if '--description' in args:
        match = re.search(r'--description\s+"([^"]+)"', args)
        if match:
            description = match.group(1)

    if '--priority' in args:
        match = re.search(r'--priority\s+(\S+)', args)
        if match:
            priority = match.group(1)

    if '--labels' in args:
        match = re.search(r'--labels\s+(\S+)', args)
        if match:
            labels = match.group(1).split(',')

    # Create the issue
    result = jira.create_issue(
        summary=title,
        description=description,
        assignee=assignee,
        labels=labels if labels else None,
        priority=priority
    )

    issue_key = result.get('key')
    print(f"Created: {issue_key}")
    print(f"URL: {jira.config['instance_url']}/browse/{issue_key}")

    # Add watchers if specified
    if watchers:
        for w in watchers:
            try:
                jira.add_watchers(issue_key, w)
                print(f"  Added watcher: {w}")
            except Exception as e:
                print(f"  Warning: Could not add watcher {w}: {e}")

elif args.startswith('assign '):
    # /jira assign DA-123 username
    parts = args.split()
    if len(parts) >= 3:
        issue_key = parts[1]
        assignee = parts[2]
        jira.assign_issue(issue_key, assignee)
        print(f"Assigned {issue_key} to {assignee}")

elif args.startswith('comment '):
    # /jira comment DA-123 "comment text"
    match = re.search(r'comment\s+(\S+)\s+"([^"]+)"', args)
    if match:
        issue_key = match.group(1)
        comment_text = match.group(2)
        jira.add_comment(issue_key, comment_text)
        print(f"Added comment to {issue_key}")

elif args.startswith('transition '):
    # /jira transition DA-123 "Done"
    match = re.search(r'transition\s+(\S+)\s+"([^"]+)"', args)
    if match:
        issue_key = match.group(1)
        status = match.group(2)
        jira.transition_issue(issue_key, status)
        print(f"Transitioned {issue_key} to '{status}'")

elif args.startswith('search '):
    # /jira search "JQL query"
    match = re.search(r'search\s+"([^"]+)"', args)
    if match:
        jql = match.group(1)
    else:
        jql = args.replace('search ', '')

    issues = jira.search(jql, max_results=10)
    print(f"Found {len(issues)} issues:\n")
    for issue in issues:
        key = issue.get('key')
        fields = issue.get('fields', {})
        summary = fields.get('summary', 'N/A')
        status = fields.get('status', {}).get('name', 'N/A')
        assignee = fields.get('assignee', {})
        assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
        print(f"  {key}: {summary}")
        print(f"    Status: {status} | Assignee: {assignee_name}")
        print()

elif args.startswith('view '):
    # /jira view DA-123
    issue_key = args.split()[1]
    issue = jira.get_issue(issue_key)
    fields = issue.get('fields', {})

    print(f"=== {issue_key}: {fields.get('summary', 'N/A')} ===")
    print(f"Status: {fields.get('status', {}).get('name', 'N/A')}")
    print(f"Type: {fields.get('issuetype', {}).get('name', 'N/A')}")
    print(f"Priority: {fields.get('priority', {}).get('name', 'N/A')}")
    assignee = fields.get('assignee')
    print(f"Assignee: {assignee.get('displayName', 'N/A') if assignee else 'Unassigned'}")
    reporter = fields.get('reporter')
    print(f"Reporter: {reporter.get('displayName', 'N/A') if reporter else 'N/A'}")
    print(f"Created: {fields.get('created', 'N/A')[:10]}")
    print(f"Updated: {fields.get('updated', 'N/A')[:10]}")
    print(f"URL: {jira.config['instance_url']}/browse/{issue_key}")

else:
    print("Unknown command. Available actions:")
    print("  /jira test                           - Test connection")
    print("  /jira create \"Title\" [--options]     - Create ticket")
    print("  /jira assign DA-123 username         - Assign ticket")
    print("  /jira comment DA-123 \"text\"          - Add comment")
    print("  /jira transition DA-123 \"Status\"     - Change status")
    print("  /jira search \"JQL query\"             - Search tickets")
    print("  /jira view DA-123                    - View ticket")
```

## Create Options

| Option | Description | Example |
|--------|-------------|---------|
| `--assignee` | Assign to user | `--assignee chandler` |
| `--watchers` | Add watchers (comma-separated) | `--watchers marshall,chris` |
| `--description` | Issue description | `--description "Details here"` |
| `--priority` | Priority level | `--priority High` |
| `--labels` | Labels (comma-separated) | `--labels bug,urgent` |
| `--type` | Issue type | `--type Bug` |

## Configuration

Config file: `~/.claude/config/jira-config.json`

```json
{
  "instance_url": "https://YOUR_INSTANCE.atlassian.net",
  "defaults": {
    "project_key": "PROJ",
    "project_name": "Your Project",
    "issue_type": "Task"
  },
  "users": {
    "username": {"account_id": "...", "display_name": "Your Name"}
  }
}
```

## Adding Team Members

To add a team member:

1. Search for their account ID:
```bash
curl -s "https://YOUR_INSTANCE.atlassian.net/rest/api/3/user/search?query=username" \
  -u "email:token" | python3 -m json.tool
```

2. Add to `~/.claude/config/jira-config.json` under `users`:
```json
"username": {
  "account_id": "712020:xxxxx",
  "display_name": "Display Name"
}
```

## Common JQL Queries

| Query | Description |
|-------|-------------|
| `project = DA` | All DA tickets |
| `project = DA AND status != Done` | Open DA tickets |
| `assignee = currentUser()` | My tickets |
| `created >= -7d` | Created in last 7 days |
| `labels = claude-code` | Labeled claude-code |

## Related Commands

- `/connect-jira` - JIRA API reference patterns
- `/report jira` - Query JIRA data from PostgreSQL
