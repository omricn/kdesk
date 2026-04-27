from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Delete all changes and reset the ID sequence to 1'

    def handle(self, *args, **options):
        from changes.models import Change, ChangeAttachment

        n = ChangeAttachment.objects.all().delete()
        self.stdout.write(f'Attachments deleted: {n}')

        count = Change.objects.count()
        Change.objects.all().delete()
        self.stdout.write(f'Changes deleted: {count}')

        with connection.cursor() as c:
            c.execute("SELECT setval(pg_get_serial_sequence('changes_change','id'), 1, false)")
        self.stdout.write(self.style.SUCCESS('Sequence reset — next change will be #1'))
