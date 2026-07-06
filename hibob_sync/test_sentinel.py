from django.test import TestCase
from hibob_sync.models import ProvisioningRequest, VerificationResult


class VerificationResultModelTests(TestCase):
    def test_create_and_defaults(self):
        req = ProvisioningRequest.objects.create(
            first_name='Luis', last_name='Diaz', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed',
        )
        vr = VerificationResult.objects.create(
            kind='provisioning', provisioning_request=req, overall='pending',
            checks=[{'key': 'x', 'label': 'X', 'status': 'pass', 'detail': ''}],
        )
        self.assertEqual(vr.overall, 'pending')
        self.assertEqual(vr.attempts, 0)
        self.assertEqual(vr.remediations, [])
        self.assertEqual(req.verifications.first(), vr)


from hibob_sync import sentinel


class FakeGraph:
    def __init__(self, user, group_ids):
        self._user = user
        self._group_ids = {g.lower() for g in group_ids}
    def get_user(self, upn):
        return self._user
    def get_user_group_identifiers(self, upn):
        return set(self._group_ids)


class ProvisioningChecksTests(TestCase):
    def _req(self, **kw):
        base = dict(
            first_name='Luis', last_name='Diaz', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed', work_email='ldiaz@kramerav.com',
            manager_email='mgr@kramerav.com',
            m365_groups=['Joiners', 'Microsoft 365 E5 Users', 'CHL_All@kramerav.com'],
            create_priority_ticket=False, create_salesforce_ticket=False,
        )
        base.update(kw)
        return ProvisioningRequest.objects.create(**base)

    def test_all_pass_when_everything_present(self):
        req = self._req()
        g = FakeGraph({'accountEnabled': True, 'mail': 'ldiaz@kramerav.com'},
                      ['joiners', 'microsoft 365 e5 users', 'chl_all@kramerav.com'])
        by = {c['key']: c for c in sentinel.verify_provisioning_checks(req, g)}
        self.assertEqual(by['entra_user']['status'], 'pass')
        self.assertEqual(by['m365_groups']['status'], 'pass')
        self.assertEqual(by['mailbox']['status'], 'pass')
        self.assertEqual(by['creds_email']['status'], 'pass')

    def test_missing_group_fails_and_names_it(self):
        req = self._req()
        g = FakeGraph({'accountEnabled': True, 'mail': 'ldiaz@kramerav.com'},
                      ['joiners', 'microsoft 365 e5 users'])
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, g)}
        self.assertEqual(checks['m365_groups']['status'], 'fail')
        self.assertIn('CHL_All@kramerav.com', checks['m365_groups']['detail'])

    def test_missing_user_marks_dependent_checks_unknown(self):
        req = self._req()
        g = FakeGraph(None, [])
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, g)}
        self.assertEqual(checks['entra_user']['status'], 'fail')
        self.assertEqual(checks['m365_groups']['status'], 'unknown')

    def test_graph_error_is_unknown_not_fail(self):
        req = self._req()
        class Boom:
            def get_user(self, upn): raise RuntimeError('graph down')
            def get_user_group_identifiers(self, upn): raise RuntimeError('graph down')
        checks = {c['key']: c for c in sentinel.verify_provisioning_checks(req, Boom())}
        self.assertEqual(checks['entra_user']['status'], 'unknown')


from unittest.mock import patch, MagicMock


class RunSentinelTests(TestCase):
    def _req(self):
        return ProvisioningRequest.objects.create(
            first_name='A', last_name='B', department='Sales', division='Sales',
            country='Chile', region='LATAM', status='completed', work_email='ab@kramerav.com',
            manager_email='m@kramerav.com', m365_groups=['Joiners'],
        )

    def test_all_pass_sets_ok_no_escalation(self):
        from tasks.scheduled import run_sentinel_verification
        req = self._req()
        checks = [{'key': 'k', 'label': 'K', 'status': 'pass', 'detail': ''}]
        with patch('hibob_sync.sentinel.verify_provisioning_checks', return_value=checks), \
             patch('tasks.scheduled._sentinel_escalate') as esc, \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            run_sentinel_verification('provisioning', req.id)
        vr = req.verifications.first()
        self.assertEqual(vr.overall, 'ok')
        esc.assert_not_called()

    def test_fail_escalates_and_records(self):
        from tasks.scheduled import run_sentinel_verification
        req = self._req()
        checks = [{'key': 'entra_user', 'label': 'x', 'status': 'fail', 'detail': 'gone'}]
        with patch('hibob_sync.sentinel.verify_provisioning_checks', return_value=checks), \
             patch('tasks.scheduled._sentinel_escalate') as esc, \
             patch('integrations.graph_client.get_client', return_value=MagicMock()):
            run_sentinel_verification('provisioning', req.id)
        vr = req.verifications.first()
        self.assertEqual(vr.overall, 'escalated')
        esc.assert_called_once()


class OffboardingChecksTests(TestCase):
    def test_termination_tickets_detected(self):
        from tickets.models import TicketCategory, TicketSubCategory, Ticket
        from hibob_sync.models import OffboardingRequest
        from hibob_sync import sentinel
        cat, _ = TicketCategory.objects.get_or_create(name='IT')
        for sub in ('Priority', 'Salesforce'):
            sc, _ = TicketSubCategory.objects.get_or_create(category=cat, name=sub)
            Ticket.objects.create(title=f'TERMINATE USER - {sub} - Jane Roe', subcategory=sc,
                                  requester_email='jane@kramerav.com', requester_name='Jane Roe')
        req = OffboardingRequest.objects.create(
            employee_email='jane@kramerav.com', employee_name='Jane Roe', status='completed',
        )
        class G:
            def get_user(self, upn): return None  # treated as removed -> pass
        checks = {c['key']: c for c in sentinel.verify_offboarding_checks(req, G())}
        self.assertEqual(checks['term_ticket_priority']['status'], 'pass')
        self.assertEqual(checks['term_ticket_salesforce']['status'], 'pass')
