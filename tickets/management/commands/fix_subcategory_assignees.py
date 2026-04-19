from django.core.management.base import BaseCommand

# subcategory name → assignee email
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
    help = 'Set subcategory assignees and ensure those users are marked as admins.'

    def handle(self, *args, **options):
        from tickets.models import TicketSubCategory
        from users.models import User

        for sub_name, email in ASSIGNMENTS.items():
            try:
                user = User.objects.get(email=email)

                # Ensure the user is an active admin so they appear in the
                # assignee dropdown (same state as local dev).
                changed = False
                if not user.is_admin:
                    user.is_admin = True
                    user.is_staff = True
                    changed = True
                if not user.is_active:
                    user.is_active = True
                    changed = True
                if changed:
                    user.save(update_fields=['is_admin', 'is_staff', 'is_active'])

                n = TicketSubCategory.objects.filter(name=sub_name).update(assignee=user)
                self.stdout.write(
                    f'  [{sub_name}] -> {user.display_name} <{email}> '
                    f'(pk={user.pk}, is_admin={user.is_admin}, rows={n})'
                )
            except User.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'  [{sub_name}] SKIPPED — user not found: {email}'
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f'  [{sub_name}] ERROR: {exc}'
                ))

        self.stdout.write(self.style.SUCCESS('fix_subcategory_assignees done.'))
