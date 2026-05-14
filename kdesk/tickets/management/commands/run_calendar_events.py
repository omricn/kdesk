from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create calendar events for a change request'

    def add_arguments(self, parser):
        parser.add_argument('change_pk', type=int)

    def handle(self, *args, **options):
        from tasks.scheduled import create_change_calendar_events
        create_change_calendar_events(options['change_pk'])
        self.stdout.write(self.style.SUCCESS(f"Done — calendar events created for change #{options['change_pk']}"))
