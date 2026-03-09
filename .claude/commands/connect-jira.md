# Connect JIRA - Atlassian API Pattern

**Purpose**: JIRA API connection patterns for issues and project data
**Usage**: `/connect-jira`

## Required Configuration

| Variable | Description | Source |
|----------|-------------|--------|
| `${JIRA_INSTANCE_URL}` | Atlassian instance URL | CLAUDE.md |
| `${JIRA_EMAIL}` | User email address | CLAUDE.md |
| `${JIRA_API_KEY}` | API key/token | CLAUDE.md |

## Note: MCP Server Available

You have access to the Atlassian MCP server (`mcp__atlassian__*` tools) which is often easier than raw API calls. Use `/connect-jira` for reference patterns when MCP doesn't cover your use case.

## curl Patterns

### Get Issue by Key
```bash
curl -s -X GET "${JIRA_INSTANCE_URL}/rest/api/3/issue/GEN-1234" \
  -u "${JIRA_EMAIL}:${JIRA_API_KEY}" \
  -H "Content-Type: application/json" | python3 -m json.tool
```

### Search Issues (JQL)
```bash
curl -s -X GET "${JIRA_INSTANCE_URL}/rest/api/3/search?jql=project=GEN%20AND%20status!=Done&maxResults=10" \
  -u "${JIRA_EMAIL}:${JIRA_API_KEY}" \
  -H "Content-Type: application/json" | python3 -m json.tool
```

### Get Projects
```bash
curl -s -X GET "${JIRA_INSTANCE_URL}/rest/api/3/project" \
  -u "${JIRA_EMAIL}:${JIRA_API_KEY}" \
  -H "Content-Type: application/json" | python3 -m json.tool
```

### Get Current User
```bash
curl -s -X GET "${JIRA_INSTANCE_URL}/rest/api/3/myself" \
  -u "${JIRA_EMAIL}:${JIRA_API_KEY}" \
  -H "Content-Type: application/json" | python3 -m json.tool
```

## Python Connection Pattern

```python
# ABOUTME: JIRA API connection using requests
# ABOUTME: Basic auth with email + API key

import requests
from requests.auth import HTTPBasicAuth
from typing import Optional, Dict, List, Any
import urllib.parse

JIRA_EMAIL = "${JIRA_EMAIL}"  # From CLAUDE.md
JIRA_API_KEY = "${JIRA_API_KEY}"  # From CLAUDE.md
JIRA_BASE_URL = "${JIRA_INSTANCE_URL}/rest/api/3"


def get_jira_auth() -> HTTPBasicAuth:
    """Get JIRA basic auth object."""
    return HTTPBasicAuth(JIRA_EMAIL, JIRA_API_KEY)


def jira_get(endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Make GET request to JIRA API."""
    url = f"{JIRA_BASE_URL}/{endpoint}"
    response = requests.get(
        url,
        auth=get_jira_auth(),
        headers={"Content-Type": "application/json"},
        params=params
    )
    response.raise_for_status()
    return response.json()


def get_issue(issue_key: str) -> Dict[str, Any]:
    """Get issue by key (e.g., GEN-1234)."""
    return jira_get(f"issue/{issue_key}")


def search_issues(jql: str, max_results: int = 50, fields: List[str] = None) -> List[Dict]:
    """Search issues using JQL."""
    params = {
        "jql": jql,
        "maxResults": max_results
    }
    if fields:
        params["fields"] = ",".join(fields)

    result = jira_get("search", params)
    return result.get("issues", [])


def get_projects() -> List[Dict]:
    """Get all accessible projects."""
    return jira_get("project")


def get_project(project_key: str) -> Dict[str, Any]:
    """Get project by key."""
    return jira_get(f"project/{project_key}")


# Example usage
if __name__ == "__main__":
    # Get current user
    me = jira_get("myself")
    print(f"Connected as: {me.get('displayName')}")

    # Search recent issues
    issues = search_issues("project = GEN ORDER BY created DESC", max_results=5)
    for issue in issues:
        key = issue.get("key")
        summary = issue.get("fields", {}).get("summary", "N/A")
        print(f"{key}: {summary}")
```

## Quick One-Liner Test

```bash
curl -s "${JIRA_INSTANCE_URL}/rest/api/3/myself" \
  -u "${JIRA_EMAIL}:${JIRA_API_KEY}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('JIRA Connected:', d.get('displayName', 'Unknown'))"
```

## Common JQL Queries

| Query | Purpose |
|-------|---------|
| `project = GEN` | All issues in GEN project |
| `project = GEN AND status = "In Progress"` | In-progress issues |
| `project = GEN AND assignee = currentUser()` | My issues |
| `project = GEN AND created >= -7d` | Created in last 7 days |
| `project = GEN AND updated >= -1d` | Updated in last day |
| `project = GEN AND type = Bug` | All bugs |

## Common Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/myself` | GET | Current user info |
| `/project` | GET | List projects |
| `/project/{key}` | GET | Get project |
| `/issue/{key}` | GET | Get issue |
| `/search?jql=...` | GET | Search issues |
| `/issue` | POST | Create issue |
| `/issue/{key}` | PUT | Update issue |
| `/issue/{key}/transitions` | GET/POST | Get/execute transitions |

## MCP Server Alternative

For most JIRA operations, use the built-in MCP tools:

```
mcp__atlassian__getJiraIssue
mcp__atlassian__searchJiraIssuesUsingJql
mcp__atlassian__createJiraIssue
mcp__atlassian__getVisibleJiraProjects
```

These are already authenticated and handle pagination automatically.

## Related Commands

- `/connect-apis` - Master API reference
