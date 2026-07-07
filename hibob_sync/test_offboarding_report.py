import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from hibob_sync.models import OffboardingRequest

API_KEY = 'test-sync-key'
REPORT_URL = '/hibob-sync/api/offboarding/report/'


@override_settings(HIBOB_SYNC_API_KEY=API_KEY)
class OffboardingReportStatusTests(TestCase):
    def _claimed_req(self, **kw):
        base = dict(
            employee_email='jdoe@kramerav.com',
            employee_name='John Doe',
            direct_manager='Jane Boss',
            country_origin='Israel',
            status='claimed',
        )
        base.update(kw)
        return OffboardingRequest.objects.create(**base)

    def _post(self, body):
        return self.client.post(
            REPORT_URL,
            data=json.dumps(body),
            content_type='application/json',
            **{'HTTP_X_SYNC_API_KEY': API_KEY},
        )

    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    @patch('tasks.scheduled.run_sentinel_verification')
    def test_litigation_hold_uncleared_maps_to_review_needed(self, mock_sentinel, mock_notify, mock_comment):
        req = self._claimed_req()
        resp = self._post({
            'req_id': req.id,
            'success': False,
            'message': 'LITIGATION_HOLD_UNCLEARED: jdoe@kramerav.com — hold not cleared within 15 min',
            'log': 'irrelevant',
        })
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'review_needed')
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args.kwargs.get('outcome')
                         or mock_notify.call_args.args[1], 'hold_review')
        mock_sentinel.assert_not_called()

    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    def test_plain_failure_still_maps_to_failed(self, mock_notify, mock_comment):
        req = self._claimed_req()
        with patch('tasks.scheduled.run_sentinel_verification'):
            resp = self._post({
                'req_id': req.id,
                'success': False,
                'message': 'Unexpected error: boom',
            })
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'failed')

    @patch('hibob_sync.views._send_manager_onedrive_notification')
    @patch('hibob_sync.views._create_offboarding_system_tickets')
    @patch('hibob_sync.views._post_offboarding_ticket_comment')
    @patch('hibob_sync.views._send_offboarding_notification')
    def test_success_still_maps_to_completed(self, mock_notify, mock_comment,
                                             mock_tickets, mock_od):
        req = self._claimed_req()
        with patch('tasks.scheduled.run_sentinel_verification'):
            resp = self._post({'req_id': req.id, 'success': True, 'message': 'done'})
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'completed')
