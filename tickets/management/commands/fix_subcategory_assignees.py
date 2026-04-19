from django.core.management.base import BaseCommand

ASSIGNMENTS = {
    'Priority':       'asaban@kramerav.com',
    'BI':             'sdekner@kramerav.com',
    'Salesforce':     'jsuissa@kramerav.com',
    'Kramer-Website': 'sc-aalon@kramerav.com',
    'Infra HW':       'ocohen@kramerav.com',
    'Infra NET':      'ocohen@kramerav.com',
    'Infra SW':       'ocohen@kramerav.com',
}


class Command(BaseCommand):
    help = 'Ensure subcategory → assignee mappings are set in the database.'

    def handle(self, *args, **options):
        from tickets.models import TicketSubCategory
        from users.models import User

        for sub_name, email in ASSIGNMENTS.items():
            try:
                user = User.objects.get(email=email)
                n = TicketSubCategory.objects.filter(name=sub_name).update(assignee=user)
                self.stdout.write(f'  [{sub_name}] -> {email} (pk={user.pk}, rows updated={n})')
            except User.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'  [{sub_name}] SKIPPED — user not found: {email}'))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  [{sub_name}] ERROR: {exc}'))

        self.stdout.write(self.style.SUCCESS('fix_subcategory_assignees complete.'))
