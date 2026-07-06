from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from hibob_sync.models import ProvisioningRequest
from hibob_sync import sentinel_diagnose


class DiagnoseTests(TestCase):
    def _req(self):
        return ProvisioningRequest.objects.create(
            first_name='A', last_name='B', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='failed', work_email='ab@kramerav.com',
        )

    @override_settings(ANTHROPIC_API_KEY='')
    def test_no_key_returns_empty(self):
        req = self._req()
        checks = [{'key': 'x', 'label': 'X', 'status': 'fail', 'detail': 'gone'}]
        with patch('hibob_sync.sentinel_diagnose._call_claude') as call:
            out = sentinel_diagnose.diagnose('provisioning', req, checks, 'log')
        self.assertEqual(out, '')
        call.assert_not_called()

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_with_key_calls_claude_and_returns_text(self):
        req = self._req()
        checks = [{'key': 'x', 'label': 'X', 'status': 'fail', 'detail': 'gone'}]
        with patch('hibob_sync.sentinel_diagnose._call_claude', return_value='Root cause: X.') as call:
            out = sentinel_diagnose.diagnose('provisioning', req, checks, 'log tail')
        self.assertEqual(out, 'Root cause: X.')
        call.assert_called_once()

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_call_failure_degrades_to_empty(self):
        req = self._req()
        with patch('hibob_sync.sentinel_diagnose._call_claude', side_effect=RuntimeError('api down')):
            out = sentinel_diagnose.diagnose('provisioning', req, [{'key': 'x', 'label': 'X', 'status': 'fail', 'detail': ''}], '')
        self.assertEqual(out, '')


class EscalateDiagnosisTests(TestCase):
    def _req(self):
        return ProvisioningRequest.objects.create(
            first_name='A', last_name='B', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='failed', work_email='ab@kramerav.com',
            manager_email='m@kramerav.com', m365_groups=['Joiners'],
        )

    def _vr(self, req):
        from hibob_sync.models import VerificationResult
        return VerificationResult.objects.create(
            kind='provisioning', provisioning_request=req, overall='escalated',
            checks=[{'key': 'entra_user', 'label': 'Entra user', 'status': 'fail', 'detail': 'gone'}],
        )

    def test_escalate_stores_diagnosis_on_vr(self):
        from tasks.scheduled import _sentinel_escalate
        req = self._req()
        vr = self._vr(req)
        with patch('hibob_sync.sentinel_diagnose.diagnose', return_value='Root cause: user missing.'), \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            _sentinel_escalate('provisioning', req, vr)
        vr.refresh_from_db()
        self.assertEqual(vr.diagnosis, 'Root cause: user missing.')

    def test_escalate_no_diagnosis_does_not_crash_and_leaves_blank(self):
        from tasks.scheduled import _sentinel_escalate
        req = self._req()
        vr = self._vr(req)
        with patch('hibob_sync.sentinel_diagnose.diagnose', return_value=''), \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            _sentinel_escalate('provisioning', req, vr)
        vr.refresh_from_db()
        self.assertEqual(vr.diagnosis, '')
