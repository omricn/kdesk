from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Delete all tickets + changes and reset ID sequences to 1'

    def handle(self, *args, **options):
        from tickets.models import (
            Ticket, TicketComment, TicketAttachment, TicketHistory,
            TicketEmail, EmailLog,
        )
        from changes.models import Change, ChangeAttachment

        # ── Changes ───────────────────────────────────────────────────────────
        n, _ = ChangeAttachment.objects.all().delete()
        self.stdout.write(f'Change attachments deleted: {n}')
        n = Change.objects.count()
        Change.objects.all().delete()
        self.stdout.write(f'Changes deleted: {n}')

        # ── Tickets (cascade handles most relations) ──────────────────────────
        n, _ = EmailLog.objects.all().delete()
        self.stdout.write(f'Email log entries deleted: {n}')
        n = Ticket.objects.count()
        Ticket.objects.all().delete()
        self.stdout.write(f'Tickets deleted: {n} (comments, attachments, history cascaded)')

        # ── Reset sequences ───────────────────────────────────────────────────
        with connection.cursor() as c:
            c.execute("SELECT setval(pg_get_serial_sequence('tickets_ticket','id'), 1, false)")
            c.execute("SELECT setval(pg_get_serial_sequence('changes_change','id'), 1, false)")

        self.stdout.write(self.style.SUCCESS(
            'Done. Next ticket → #0001, next change → #0001'
        ))
