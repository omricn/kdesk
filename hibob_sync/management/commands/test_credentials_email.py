from django.core.management.base import BaseCommand

from hibob_sync.models import ProvisioningRequest
from hibob_sync.views import _send_manager_credentials_email


class Command(BaseCommand):
    help = 'Create a mock provisioning request and send the credentials email to a manager.'

    def add_arguments(self, parser):
        parser.add_argument('--manager-email', default='ocohen@kramerav.com')
        parser.add_argument('--employee-email', default='test.newuser@kramerav.com')
        parser.add_argument('--password', default='Tu12341234!@')

    def handle(self, *args, **options):
        req = ProvisioningRequest.objects.create(
            first_name='Test',
            last_name='NewUser',
            department='IT',
            division='Technology',
            country='Israel',
            region='HQ',
            reports_to='Omri Cohen',
            job_title='Test Account',
            status='completed',
            work_email=options['employee_email'],
            temp_password=options['password'],
            manager_email=options['manager_email'],
        )
        _send_manager_credentials_email(req)
        creds_url = f'https://kdesk.kramerav.com/hibob-sync/credentials/{req.id}/'
        self.stdout.write(self.style.SUCCESS(
            f'Done. req_id={req.id}\n'
            f'Email sent to: {options["manager_email"]}\n'
            f'Credentials URL: {creds_url}'
        ))
