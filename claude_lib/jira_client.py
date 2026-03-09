# ABOUTME: JIRA client library for Claude Code /jira skill
# ABOUTME: Handles authentication, issue CRUD, and JQL search operations

import json
import requests
from pathlib import Path
from requests.auth import HTTPBasicAuth
from typing import Optional, Dict, List, Any


class JiraClient:
    """JIRA API client for Claude Code integration."""

    def __init__(self, config_path: str = None, token_path: str = None):
        """Initialize JIRA client with config and token files."""
        config_path = config_path or Path.home() / '.claude/config/jira-config.json'
        token_path = token_path or Path.home() / '.claude/secrets/jira-token'

        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        # Load API token
        with open(token_path, 'r') as f:
            self.api_token = f.read().strip()

        self.base_url = f"{self.config['instance_url']}/rest/api/3"
        self.email = self.config['email']

    def _auth(self) -> HTTPBasicAuth:
        """Get HTTP basic auth object."""
        return HTTPBasicAuth(self.email, self.api_token)

    def _headers(self) -> Dict[str, str]:
        """Get request headers."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Make GET request to JIRA API."""
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(
            url,
            auth=self._auth(),
            headers=self._headers(),
            params=params
        )
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, data: Dict) -> Dict[str, Any]:
        """Make POST request to JIRA API."""
        url = f"{self.base_url}/{endpoint}"
        response = requests.post(
            url,
            auth=self._auth(),
            headers=self._headers(),
            json=data
        )
        response.raise_for_status()
        return response.json()

    def _put(self, endpoint: str, data: Dict) -> Dict[str, Any]:
        """Make PUT request to JIRA API."""
        url = f"{self.base_url}/{endpoint}"
        response = requests.put(
            url,
            auth=self._auth(),
            headers=self._headers(),
            json=data
        )
        response.raise_for_status()
        # PUT may return empty response
        if response.text:
            return response.json()
        return {}

    def get_myself(self) -> Dict[str, Any]:
        """Get current authenticated user."""
        return self._get("myself")

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get issue by key (e.g., DA-123)."""
        return self._get(f"issue/{issue_key}")

    def search(self, jql: str, max_results: int = 50, fields: List[str] = None) -> List[Dict]:
        """Search issues using JQL."""
        params = {
            "jql": jql,
            "maxResults": max_results
        }
        if fields:
            params["fields"] = ",".join(fields)

        result = self._get("search", params)
        return result.get("issues", [])

    def create_issue(
        self,
        summary: str,
        description: Optional[str] = None,
        issue_type: Optional[str] = None,
        project_key: Optional[str] = None,
        assignee: Optional[str] = None,
        labels: Optional[List[str]] = None,
        priority: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new JIRA issue."""
        # Use defaults from config
        project_key = project_key or self.config['defaults']['project_key']
        issue_type = issue_type or self.config['defaults']['issue_type']

        # Build issue data
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type}
        }

        # Add description in Atlassian Document Format
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}]
                    }
                ]
            }

        # Resolve assignee to account ID
        if assignee:
            account_id = self._resolve_user(assignee)
            if account_id:
                fields["assignee"] = {"accountId": account_id}
            else:
                raise ValueError(f"Assignee '{assignee}' not found. Available users: {list(self.config.get('users', {}).keys())}")

        # Add labels
        if labels:
            fields["labels"] = labels

        # Add priority
        if priority:
            fields["priority"] = {"name": priority}

        return self._post("issue", {"fields": fields})

    def assign_issue(self, issue_key: str, assignee: str) -> None:
        """Assign an issue to a user."""
        account_id = self._resolve_user(assignee)
        if not account_id:
            raise ValueError(f"User not found: {assignee}")

        self._put(f"issue/{issue_key}/assignee", {"accountId": account_id})

    def add_comment(self, issue_key: str, comment: str) -> Dict[str, Any]:
        """Add a comment to an issue."""
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment}]
                    }
                ]
            }
        }
        return self._post(f"issue/{issue_key}/comment", body)

    def transition_issue(self, issue_key: str, status_name: str) -> None:
        """Transition an issue to a new status."""
        # Get available transitions
        transitions = self._get(f"issue/{issue_key}/transitions")

        # Find matching transition
        target_transition = None
        for t in transitions.get("transitions", []):
            if t.get("name", "").lower() == status_name.lower():
                target_transition = t
                break
            # Also check "to" status name
            to_status = t.get("to", {}).get("name", "")
            if to_status.lower() == status_name.lower():
                target_transition = t
                break

        if not target_transition:
            available = [t.get("name") for t in transitions.get("transitions", [])]
            raise ValueError(f"Transition '{status_name}' not found. Available: {available}")

        # Execute transition
        url = f"{self.base_url}/issue/{issue_key}/transitions"
        response = requests.post(
            url,
            auth=self._auth(),
            headers=self._headers(),
            json={"transition": {"id": target_transition["id"]}}
        )
        response.raise_for_status()

    def add_watchers(self, issue_key: str, username: str) -> None:
        """Add a watcher to an issue."""
        account_id = self._resolve_user(username)
        if not account_id:
            raise ValueError(f"User not found: {username}")

        url = f"{self.base_url}/issue/{issue_key}/watchers"
        response = requests.post(
            url,
            auth=self._auth(),
            headers=self._headers(),
            json=account_id  # Body is just the account ID string
        )
        response.raise_for_status()

    def _resolve_user(self, username: str) -> Optional[str]:
        """Resolve username to account ID using config mappings."""
        # Check config users first
        users = self.config.get("users", {})
        if username.lower() in users:
            return users[username.lower()].get("account_id")

        # Check if it's already an account ID
        if username.startswith("712020:") or username.startswith("557058:"):
            return username

        # Try API search as fallback
        try:
            results = self._get("user/search", {"query": username})
            if results:
                return results[0].get("accountId")
        except Exception:
            pass

        return None
