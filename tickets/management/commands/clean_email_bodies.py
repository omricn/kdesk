"""
One-shot: strip external img tags from stored HTML TicketEmail bodies.
Run once after deploying the _sanitize_html fix.
"""
import re
from django.core.management.base import BaseCommand
from tickets.models import TicketEmail

_EXT_IMG_RE = re.compile(r'<img[^>]+src=["\']https?://[^"\']*["\'][^>]*/?>', re.IGNORECASE)


class Command(BaseCommand):
    help = 'Strip external img tags from HTML TicketEmail bodies'

    def handle(self, *args, **options):
        qs = TicketEmail.objects.filter(body_is_html=True)
        fixed = 0
        for email in qs:
            cleaned = _EXT_IMG_RE.sub('', email.body)
            if cleaned != email.body:
                email.body = cleaned
                email.save(update_fields=['body'])
                fixed += 1
                self.stdout.write(f'  Fixed TicketEmail #{email.pk} (ticket #{email.ticket_id})')
        self.stdout.write(self.style.SUCCESS(f'Done — {fixed} email(s) cleaned.'))
