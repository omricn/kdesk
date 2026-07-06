from unittest.mock import patch
from django.test import TestCase, override_settings
from hibob_sync.models import ProvisioningRequest, VerificationResult
from hibob_sync import sentinel_issue


class IssueTests(TestCase):
    def _req(self):
        return ProvisioningRequest.objects.create(
            first_name='A', last_name='B', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='failed', work_email='ab@kramerav.com',
        )

    def _vr(self, req):
        return VerificationResult.objects.create(
            kind='provisioning', provisioning_request=req, overall='escalated',
            checks=[{'key': 'entra_user', 'label': 'Entra account', 'status': 'fail', 'detail': 'missing'}],
            diagnosis='Root cause: account never created.',
        )

    @override_settings(GITHUB_TOKEN='')
    def test_no_token_returns_empty(self):
        req = self._req(); vr = self._vr(req)
        with patch('hibob_sync.sentinel_issue._create_issue') as c:
            self.assertEqual(sentinel_issue.open_incident_issue('provisioning', req, vr), '')
        c.assert_not_called()

    @override_settings(GITHUB_TOKEN='t', GITHUB_REPO='KramerAV/kdesk')
    def test_creates_issue_and_returns_url(self):
        req = self._req(); vr = self._vr(req)
        with patch('hibob_sync.sentinel_issue._create_issue', return_value='https://github.com/x/y/issues/1') as c:
            url = sentinel_issue.open_incident_issue('provisioning', req, vr)
        self.assertEqual(url, 'https://github.com/x/y/issues/1')
        c.assert_called_once()

    def test_already_filed_dedup(self):
        req = self._req()
        VerificationResult.objects.create(kind='provisioning', provisioning_request=req,
                                          overall='escalated', issue_url='https://github.com/x/y/issues/9')
        self.assertTrue(sentinel_issue.already_filed('provisioning', req))
