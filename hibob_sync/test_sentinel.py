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
