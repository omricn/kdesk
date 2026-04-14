"""
Polls the servicedesk mailbox for new emails and creates tickets.
"""
import base64
import logging
import os
import tempfile

from django.conf import settings
from django.core.files import File
from django.utils import timezone

from .graph_client import get_client

logger = logging.getLogger(__name__)


def poll_mailbox():
    """
    Fetch unread emails from the servicedesk mailbox.
    For each new email, create a Ticket (unless already processed).
    """
    # Avoid circular imports — import models here
    from tickets.models import Ticket, TicketAttachment, EmailLog
    from tickets.views import _auto_assign

    client = get_client()
    mailbox = settings.SERVICEDESK_EMAIL

    try:
        messages = client.list_unread_messages(mailbox)
    except Exception as exc:
        logger.error(f'[EmailPoller] Failed to list messages: {exc}')
        return

    for msg in messages:
        internet_msg_id = msg.get('internetMessageId', msg['id'])

        # Skip already processed
        if EmailLog.objects.filter(message_id=internet_msg_id).exists():
            try:
                client.mark_message_read(mailbox, msg['id'])
            except Exception as exc:
                logger.warning(f'[EmailPoller] Could not mark already-processed {msg["id"]} as read: {exc}')
            continue

        ticket = None
        error_text = ''
        try:
            ticket = _create_ticket_from_message(msg, client, mailbox)
        except Exception as exc:
            error_text = str(exc)
            logger.error(f'[EmailPoller] Failed to create ticket from {internet_msg_id}: {exc}')

        EmailLog.objects.create(
            message_id=internet_msg_id,
            ticket=ticket,
            error=error_text,
        )

        # Mark as read so we don't reprocess
        try:
            client.mark_message_read(mailbox, msg['id'])
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not mark {msg["id"]} as read: {exc}')


def _create_ticket_from_message(msg, client, mailbox):
    from tickets.models import Ticket, TicketAttachment

    sender = msg.get('from', {}).get('emailAddress', {})
    requester_email = sender.get('address', 'unknown@unknown.com')
    requester_name = sender.get('name', '')

    subject = msg.get('subject', '(No Subject)').strip() or '(No Subject)'

    body_content = msg.get('body', {}).get('content', '')
    # Strip any HTML if the body is HTML
    if msg.get('body', {}).get('contentType', '').lower() == 'html':
        body_content = _html_to_text(body_content)

    from tickets.views import _set_default_category
    ticket = Ticket(
        title=subject,
        description=body_content,
        requester_email=requester_email,
        requester_name=requester_name,
        source=Ticket.SOURCE_EMAIL,
        email_message_id=msg.get('internetMessageId', msg['id']),
    )
    _set_default_category(ticket)
    ticket.save()

    # Download attachments
    if msg.get('hasAttachments'):
        try:
            attachments = client.get_message_attachments(mailbox, msg['id'])
            for att in attachments:
                if att.get('@odata.type') != '#microsoft.graph.fileAttachment':
                    continue
                filename = att.get('name', 'attachment')
                content_bytes = base64.b64decode(att.get('contentBytes', ''))
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(content_bytes)
                    tmp_path = tmp.name
                try:
                    with open(tmp_path, 'rb') as f:
                        TicketAttachment.objects.create(
                            ticket=ticket,
                            filename=filename,
                            file=File(f, name=filename),
                            file_size=len(content_bytes),
                        )
                finally:
                    os.unlink(tmp_path)
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not save attachments for ticket #{ticket.pk}: {exc}')

    # Confirm receipt to the requester
    try:
        from tasks.scheduled import send_requester_created
        send_requester_created.delay(ticket.pk)
    except Exception as exc:
        logger.warning(f'[EmailPoller] Could not queue requester confirmation: {exc}')

    logger.info(f'[EmailPoller] Created ticket #{ticket.pk} from email: {subject}')
    return ticket


def _html_to_text(html: str) -> str:
    """Very basic HTML-to-text stripping. Keeps it dependency-free."""
    import re
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return text.strip()
