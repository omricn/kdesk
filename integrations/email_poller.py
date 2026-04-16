"""
Polls the servicedesk mailbox for new emails and creates tickets.
"""
import base64
import logging
import os
import re
import tempfile

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .graph_client import get_client

logger = logging.getLogger(__name__)

TICKET_REPLY_RE = re.compile(r'\[Ticket #(\d+)\]', re.IGNORECASE)
FORWARD_RE = re.compile(r'^(fwd?|fw)\s*:', re.IGNORECASE)


def poll_mailbox():
    """
    Fetch unread emails from the servicedesk mailbox.
    For each new email, create a Ticket (unless already processed).
    """
    # Avoid circular imports — import models here
    from tickets.models import Ticket, TicketAttachment, EmailLog

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
                client.move_message_to_deleted(mailbox, msg['id'])
            except Exception as exc:
                logger.warning(f'[EmailPoller] Could not move already-processed {msg["id"]} to deleted: {exc}')
            continue

        sender = msg.get('from', {}).get('emailAddress', {})
        sender_email = sender.get('address', '').lower()
        subject = msg.get('subject', '')

        # Ignore messages sent by the servicedesk itself (delivery receipts, loops)
        is_from_servicedesk = sender_email == mailbox.lower()

        ticket = None
        error_text = ''

        # Treat forwarded emails (Fwd:/FW:) as new tickets even if subject
        # contains a ticket reference — the original ticket is just quoted context.
        is_forward = bool(FORWARD_RE.match(subject))
        reply_match = TICKET_REPLY_RE.search(subject)

        try:
            with transaction.atomic():
                if reply_match and not is_from_servicedesk and not is_forward:
                    # User reply to an existing ticket
                    ticket = _handle_ticket_reply(msg, reply_match)
                else:
                    ticket = _create_ticket_from_message(msg, client, mailbox)

                # Create EmailLog inside the same transaction so a crash between
                # ticket creation and log creation cannot produce duplicate tickets.
                EmailLog.objects.create(
                    message_id=internet_msg_id,
                    ticket=ticket,
                    error='',
                )
        except Exception as exc:
            error_text = str(exc)
            logger.error(f'[EmailPoller] Failed to process {internet_msg_id}: {exc}')
            # Still record the failure so we don't retry forever on a broken message.
            try:
                EmailLog.objects.get_or_create(
                    message_id=internet_msg_id,
                    defaults={'ticket': None, 'error': error_text},
                )
            except Exception:
                pass

        # Move to Deleted Items so we don't reprocess and mailbox stays clean
        try:
            client.move_message_to_deleted(mailbox, msg['id'])
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not move {msg["id"]} to deleted: {exc}')


def _handle_ticket_reply(msg, reply_match):
    """
    Process an incoming email that is a reply to an existing ticket.
    Updates ticket status to 'user_responded' and logs the email in TicketEmail.
    Returns the Ticket instance.
    """
    from tickets.models import Ticket, TicketEmail

    ticket_pk = int(reply_match.group(1))
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        logger.warning(f'[EmailPoller] Reply references unknown ticket #{ticket_pk}, ignoring.')
        return None

    sender = msg.get('from', {}).get('emailAddress', {})
    sender_email = sender.get('address', 'unknown@unknown.com')

    body_content = msg.get('body', {}).get('content', '')
    content_type = msg.get('body', {}).get('contentType', '').lower()
    if content_type == 'html':
        body_content = _sanitize_html(body_content)
        body_content = _html_to_plain(body_content)
    body_content = _strip_quoted_reply(body_content)

    subject = msg.get('subject', f'Re: [Ticket #{ticket_pk:04d}]').strip()

    # Log the inbound email in the correspondence record
    TicketEmail.objects.create(
        ticket=ticket,
        direction=TicketEmail.DIRECTION_RECEIVED,
        subject=subject,
        body=body_content,
        from_email=sender_email,
        to_email='',  # The mailbox — not stored separately
    )

    # Update ticket status to user_responded (unless already closed)
    if ticket.status not in ticket.TERMINAL_STATUSES:
        ticket.status = Ticket.STATUS_USER_RESPONDED
        ticket.save(update_fields=['status'])
        logger.info(f'[EmailPoller] Ticket #{ticket_pk} marked as user_responded (reply from {sender_email})')

    return ticket


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


def _html_to_plain(html: str) -> str:
    """Convert sanitized HTML to clean plain text."""
    # Replace block-level tags with newlines before stripping
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</(p|div|tr|li|h[1-6])>', '\n', html, flags=re.IGNORECASE)
    # Strip remaining tags
    html = re.sub(r'<[^>]+>', '', html)
    # Decode HTML entities
    import html as html_module
    html = html_module.unescape(html)
    # Collapse excessive blank lines
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def _strip_quoted_reply(text: str) -> str:
    """Remove the quoted original message from a reply body."""
    # Common reply separators used by Outlook, Gmail, etc.
    separators = [
        r'^_{3,}$',                          # ___ divider line
        r'^-{3,}$',                          # --- divider line
        r'^From:.*Sent:',                    # Outlook "From: ... Sent:" header
        r'^On .+ wrote:$',                   # Gmail/Apple "On ... wrote:"
        r'^\s*>',                            # Quoted lines starting with >
    ]
    pattern = re.compile('|'.join(separators), re.IGNORECASE | re.MULTILINE)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            text = '\n'.join(lines[:i]).strip()
            break
    return text


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
