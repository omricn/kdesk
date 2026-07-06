from unittest.mock import patch
from django.test import TestCase
from integrations.graph_client import GraphClient


class GraphReadTests(TestCase):
    def _client(self):
        c = GraphClient.__new__(GraphClient)   # bypass __init__/MSAL
        return c

    def test_get_user_returns_fields(self):
        c = self._client()
        with patch.object(c, 'get', return_value={'id': '1', 'accountEnabled': True, 'mail': 'a@x.com', 'displayName': 'A'}):
            u = c.get_user('a@x.com')
        self.assertTrue(u['accountEnabled'])
        self.assertEqual(u['mail'], 'a@x.com')

    def test_get_user_returns_none_on_404(self):
        import requests
        c = self._client()
        err = requests.exceptions.HTTPError(response=type('R', (), {'status_code': 404})())
        with patch.object(c, 'get', side_effect=err):
            self.assertIsNone(c.get_user('missing@x.com'))

    def test_group_identifiers_lowercased_union_of_mail_and_name(self):
        c = self._client()
        groups = [
            {'id': '1', 'mail': 'CHL_All@x.com', 'displayName': 'CHL All'},
            {'id': '2', 'mail': None, 'displayName': 'Joiners'},
        ]
        with patch.object(c, 'get_paginated', return_value=groups):
            ids = c.get_user_group_identifiers('a@x.com')
        self.assertIn('chl_all@x.com', ids)
        self.assertIn('joiners', ids)
