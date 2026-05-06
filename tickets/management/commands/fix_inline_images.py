from django.core.management.base import BaseCommand
from tickets.models import TicketAttachment


class Command(BaseCommand):
    help = 'Replace direct blob URLs with proxy URLs for inline email images'

    def handle(self, *args, **options):
        fixed = 0
        inline_atts = TicketAttachment.objects.filter(
            is_inline=True,
            content_id__gt='',
        ).select_related('ticket')

        for att in inline_atts:
            ticket = att.ticket
            if not ticket.description or not ticket.description_is_html:
                continue
            blob_url = att.file.url
            proxy_url = f'/attachments/{att.pk}/download/?inline=1'
            if blob_url in ticket.description:
                ticket.description = ticket.description.replace(blob_url, proxy_url)
                ticket.save(update_fields=['description'])
                fixed += 1
                self.stdout.write(f'  Fixed ticket #{ticket.pk} attachment {att.pk}')

        self.stdout.write(self.style.SUCCESS(f'Done. Fixed {fixed} inline image(s).'))
