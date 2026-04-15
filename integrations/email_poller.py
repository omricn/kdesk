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
    content_type = msg.get('body', {}).get('contentType', '').lower()
    is_html = content_type == 'html'

    if is_html:
        body_content = _sanitize_html(body_content)

    from tickets.views import _set_default_category
    ticket = Ticket(
        title=subject,
        description=body_content,
        description_is_html=is_html,
        requester_email=requester_email,
        requester_name=requester_name,
        source=Ticket.SOURCE_EMAIL,
        email_message_id=msg.get('internetMessageId', msg['id']),
    )
    _set_default_category(ticket)
    ticket.save()

    # Download attachments; track inline CID → URL mapping for image resolution
    cid_map = {}
    if msg.get('hasAttachments'):
        try:
            attachments = client.get_message_attachments(mailbox, msg['id'])
            for att in attachments:
                if att.get('@odata.type') != '#microsoft.graph.fileAttachment':
                    continue
                filename = att.get('name', 'attachment')
                content_bytes = base64.b64decode(att.get('contentBytes', ''))
                att_content_id = att.get('contentId', '')
                att_is_inline = att.get('isInline', False)
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(content_bytes)
                    tmp_path = tmp.name
                try:
                    with open(tmp_path, 'rb') as f:
                        ta = TicketAttachment.objects.create(
                            ticket=ticket,
                            filename=filename,
                            file=File(f, name=filename),
                            file_size=len(content_bytes),
                            content_id=att_content_id,
                            is_inline=att_is_inline,
                        )
                    if att_content_id and att_is_inline:
                        cid_map[att_content_id] = ta.file.url
                finally:
                    os.unlink(tmp_path)
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not save attachments for ticket #{ticket.pk}: {exc}')

    # Replace cid: references in HTML body with actual served file URLs
    if is_html and cid_map:
        description = ticket.description
        for cid, url in cid_map.items():
            description = description.replace(f'cid:{cid}', url)
        ticket.description = description
        ticket.save(update_fields=['description'])

    # Confirm receipt to the requester and generate AI summary
    try:
        from tasks.scheduled import send_requester_created, generate_ai_summary
        send_requester_created.delay(ticket.pk)
        generate_ai_summary.delay(ticket.pk)
    except Exception as exc:
        logger.warning(f'[EmailPoller] Could not queue post-create tasks: {exc}')

    logger.info(f'[EmailPoller] Created ticket #{ticket.pk} from email: {subject}')
    return ticket


def _sanitize_html(html: str) -> str:
    """
    Strip dangerous elements from email HTML, extract body content,
    and preserve inline images and formatting.
    """
    import re
    # Extract just the <body> content if this is a full HTML document
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        html = body_match.group(1)
    # Remove script blocks
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove style blocks (avoid polluting page CSS)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove iframes
    html = re.sub(r'<iframe[^>]*>.*?</iframe>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove javascript: hrefs
    html = re.sub(r'href\s*=\s*["\']javascript:[^"\']*["\']', 'href="#"', html, flags=re.IGNORECASE)
    # Remove on* event handlers
    html = re.sub(r'\s+on\w+\s*=\s*"[^"]*"', '', html, flags=re.IGNORECASE)
    html = re.sub(r"\s+on\w+\s*=\s*'[^']*'", '', html, flags=re.IGNORECASE)
    return html.strip()
