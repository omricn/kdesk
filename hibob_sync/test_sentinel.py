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
