"""
Microsoft Graph API client.
Uses the client credentials flow (app-only, no user login needed).
"""
import logging
import msal
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


class GraphClient:
    def __init__(self):
        self._token = None
        self._app = msal.ConfidentialClientApplication(
            client_id=settings.AZURE_CLIENT_ID,
            client_credential=settings.AZURE_CLIENT_SECRET,
            authority=f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}',
        )

    def _get_token(self):
        result = self._app.acquire_token_for_client(
            scopes=['https://graph.microsoft.com/.default']
        )
        if 'access_token' not in result:
            error = result.get('error_description', str(result))
            raise RuntimeError(f'Failed to acquire Graph token: {error}')
        return result['access_token']

    def _headers(self):
        return {'Authorization': f'Bearer {self._get_token()}', 'Content-Type': 'application/json'}

    def get(self, path, params=None):
        url = path if path.startswith('http') else f'{GRAPH_BASE}{path}'
        r = requests.get(url, headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_paginated(self, path, params=None):
        """Follows @odata.nextLink to retrieve all pages."""
        results = []
        url = f'{GRAPH_BASE}{path}'
        while url:
            data = self.get(url, params=params)
            results.extend(data.get('value', []))
            url = data.get('@odata.nextLink')
            params = None  # only pass params on first request
        return results

    def post(self, path, json_data):
        url = f'{GRAPH_BASE}{path}'
        r = requests.post(url, headers=self._headers(), json=json_data, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ── Mail ──────────────────────────────────────────────────────────────────

    def list_unread_messages(self, mailbox: str, top: int = 50):
        """Return unread messages from the given mailbox."""
        path = f'/users/{mailbox}/mailFolders/Inbox/messages'
        params = {
            '$filter': 'isRead eq false',
            '$top': top,
            '$select': 'id,subject,from,body,receivedDateTime,hasAttachments,internetMessageId',
        }
        return self.get_paginated(path, params)

    def get_message_attachments(self, mailbox: str, message_id: str):
        path = f'/users/{mailbox}/messages/{message_id}/attachments'
        data = self.get(path)
        return data.get('value', [])

    def mark_message_read(self, mailbox: str, message_id: str):
        url = f'{GRAPH_BASE}/users/{mailbox}/messages/{message_id}'
        r = requests.patch(
            url,
            headers=self._headers(),
            json={'isRead': True},
            timeout=30,
        )
        r.raise_for_status()

    def send_email(self, from_mailbox: str, to_email: str, subject: str, body_html: str):
        """Send an email from the servicedesk mailbox."""
        path = f'/users/{from_mailbox}/sendMail'
        payload = {
            'message': {
                'subject': subject,
                'body': {'contentType': 'HTML', 'content': body_html},
                'toRecipients': [{'emailAddress': {'address': to_email}}],
            }
        }
        self.post(path, payload)

    # ── Users / Groups ────────────────────────────────────────────────────────

    def get_group_id_by_name(self, group_name: str):
        data = self.get('/groups', params={'$filter': f"displayName eq '{group_name}'", '$select': 'id,displayName'})
        groups = data.get('value', [])
        if not groups:
            raise ValueError(f"Entra group '{group_name}' not found")
        return groups[0]['id']

    def get_group_members(self, group_id: str):
        """Returns all members of the given group (handles pagination)."""
        return self.get_paginated(
            f'/groups/{group_id}/members',
            params={'$select': 'id,displayName,mail,accountEnabled'},
        )

    def get_group_id_by_email(self, group_email: str):
        """Look up a group by its email address (mail-enabled security groups)."""
        data = self.get('/groups', params={
            '$filter': f"mail eq '{group_email}'",
            '$select': 'id,displayName,mail',
        })
        groups = data.get('value', [])
        if not groups:
            raise ValueError(f"Group with email '{group_email}' not found")
        return groups[0]['id']

    def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        """Check if a user is a (transitive) member of a group."""
        try:
            result = self.post(
                f'/users/{user_id}/checkMemberGroups',
                json_data={'groupIds': [group_id]},
            )
            return group_id in result.get('value', [])
        except Exception:
            return False

    def get_user_profile(self, user_access_token: str) -> dict:
        """Fetch the signed-in user's profile using their own access token."""
        r = requests.get(
            f'{GRAPH_BASE}/me',
            headers={
                'Authorization': f'Bearer {user_access_token}',
                'Content-Type': 'application/json',
            },
            params={'$select': 'id,displayName,mail,userPrincipalName'},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


# Singleton — re-used across tasks
_client = None


def get_client() -> GraphClient:
    global _client
    if _client is None:
        _client = GraphClient()
    return _client
