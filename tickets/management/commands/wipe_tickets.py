from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Delete all tickets and reset the ID sequence to 1'

    def handle(self, *args, **options):
        from tickets.models import Ticket, TicketComment, TicketHistory
        try:
            from tickets.models import TicketAttachment
            n = TicketAttachment.objects.all().delete()
            self.stdout.write(f'Attachments deleted: {n}')
        except Exception as e:
            self.stdout.write(f'Attachments: {e}')

        n = TicketHistory.objects.all().delete()
        self.stdout.write(f'History deleted: {n}')

        n = TicketComment.objects.all().delete()
        self.stdout.write(f'Comments deleted: {n}')

        count = Ticket.objects.count()
        Ticket.objects.all().delete()
        self.stdout.write(f'Tickets deleted: {count}')

        with connection.cursor() as c:
            c.execute("SELECT setval(pg_get_serial_sequence('tickets_ticket','id'), 1, false)")
        self.stdout.write(self.style.SUCCESS('Sequence reset — next ticket will be #1'))
