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


def _fmt_recipients(recipients):
    parts = []
    for r in recipients:
        addr = r.get('emailAddress', {})
        email = addr.get('address', '')
        name  = addr.get('name', '')
        if name and name.lower() != email.lower():
            parts.append(f'{name} <{email}>')
        elif email:
            parts.append(email)
    return ', '.join(parts)


FORWARD_RE      = re.compile(r'^(fwd?|fw)\s*:', re.IGNORECASE)
# Strips one or more RE:/FW:/Fwd: prefixes from a subject (handles stacked prefixes)
_SUBJECT_PREFIX_RE = re.compile(r'^((re|fwd?)\s*:\s*)+', re.IGNORECASE)

# Subjects that unmistakably indicate an auto-reply / OOF message
_AUTOREPLY_SUBJECT_RE = re.compile(
    r'\b(out\s+of\s+(office|the\s+office)|automatic\s*reply|auto[\s\-]?reply|'
    r'autoreply|ooo\b|vacation\s+notice|away\s+from\s+(the\s+)?office|'
    r'i\s*(\'?m|am)\s+away|hors\s+du\s+bureau|automatische\s+antwort|'
    r'abwesenheits|fuera\s+de\s+la\s+oficina|absence\s+du\s+bureau)\b',
    re.IGNORECASE,
)


def _is_autoreply(msg) -> bool:
    """Return True if the email is an automatic reply (OOF, vacation, etc.)."""
    subject = msg.get('subject', '')
    if _AUTOREPLY_SUBJECT_RE.search(subject):
        return True

    # Header-based detection — present when Graph API returns internetMessageHeaders
    headers = {
        h['name'].lower(): h['value'].lower()
        for h in msg.get('internetMessageHeaders', [])
    }

    # RFC 3834 — the standard header for automated responses
    auto_submitted = headers.get('auto-submitted', '')
    if auto_submitted and auto_submitted != 'no':
        return True

    # Microsoft Exchange / Outlook OOF header
    if headers.get('x-auto-response-suppress'):
        return True

    # Generic autoreply flag used by some MTAs
    if headers.get('x-autoreply') == 'yes':
        return True

    # Precedence: auto-reply / junk are genuine autoreply signals.
    # Do NOT include 'bulk' or 'list' — those are mass-mail markers used by
    # legitimate notification systems (HiBob, Jira, Monday, etc.) and would
    # cause real action-required emails to be silently discarded.
    if headers.get('precedence') in ('junk', 'auto-reply'):
        return True

    return False


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

        # Skip already processed — but if the linked ticket was deleted (ticket=NULL),
        # remove the stale log entry and re-process so a new ticket is created.
        existing_log = EmailLog.objects.filter(message_id=internet_msg_id).first()
        if existing_log:
            # Always skip — whether the ticket exists, was deleted, or was intentionally discarded.
            # Never re-process a message that already has an EmailLog entry; doing so would create
            # duplicate tickets and fire duplicate confirmation emails when tickets are bulk-deleted.
            try:
                client.move_message_to_deleted(mailbox, msg['id'])
            except Exception as exc:
                logger.warning(f'[EmailPoller] Could not move already-processed {msg["id"]} to deleted: {exc}')
            continue

        sender = msg.get('from', {}).get('emailAddress', {})
        sender_email = sender.get('address', '').lower()
        subject = msg.get('subject', '')

        # Hard-skip any email sent FROM the servicedesk mailbox itself.
        # These are change broadcast self-copies, delivery receipts, or loop artifacts.
        # Do NOT create tickets from them — that is what causes the mail loop.
        if sender_email == mailbox.lower():
            logger.info(f'[EmailPoller] Skipping self-email (from=servicedesk): {subject!r}')
            try:
                client.move_message_to_deleted(mailbox, msg['id'])
            except Exception as exc:
                logger.warning(f'[EmailPoller] Could not move self-email to deleted: {exc}')
            try:
                EmailLog.objects.get_or_create(
                    message_id=internet_msg_id,
                    defaults={'ticket': None, 'error': 'self-email skipped'},
                )
            except Exception:
                pass
            continue

        ticket = None
        error_text = ''

        # Treat forwarded emails (Fwd:/FW:) as new tickets even if subject
        # contains a ticket reference — the original ticket is just quoted context.
        is_forward = bool(FORWARD_RE.match(subject))  # FW:/Fwd: → always new ticket
        reply_match = TICKET_REPLY_RE.search(subject)
        conversation_id = msg.get('conversationId', '')

        try:
            with transaction.atomic():
                # Resolve which existing ticket this message belongs to, if any.
                existing_ticket = None
                if not is_forward:
                    if reply_match:
                        # Explicit [Ticket #N] in subject (reply to a Kdesk notification)
                        try:
                            existing_ticket = Ticket.objects.filter(
                                pk=int(reply_match.group(1))
                            ).first()
                        except (ValueError, Exception):
                            pass
                    elif conversation_id:
                        # Reply to the original email thread — match by Graph conversationId
                        existing_ticket = Ticket.objects.filter(
                            email_conversation_id=conversation_id
                        ).first()

                    # Fallback: email whose conversationId didn't match any open ticket.
                    # Covers:
                    # 1. Ticket predates email_conversation_id (field is blank)
                    # 2. Original ticket was created from a FW: — its conversationId differs
                    #    from the original thread that everyone replies to
                    # 3. Stacked prefixes (Re: FW: Re: …) on both stored titles and replies
                    # 4. Ticket was created from a RE:-prefixed email; the follow-up arrives
                    #    without any prefix (bare subject matches stored RE: title when stripped)
                    #
                    # Strategy: normalize BOTH the incoming subject AND stored ticket titles
                    # by stripping all RE:/FW: prefixes, then compare. Use Python-level
                    # iteration (open ticket count is small) so we can normalize both sides.
                    # Guard: only route if exactly one open non-merged ticket matches.
                    bare_incoming = _SUBJECT_PREFIX_RE.sub('', subject).strip().lower()
                    if not existing_ticket and bare_incoming:
                        open_tickets = list(
                            Ticket.objects.filter(merged_into__isnull=True)
                            .exclude(status=Ticket.STATUS_CLOSED)
                            .only('pk', 'title', 'requester_email')
                        )
                        matches = [
                            t for t in open_tickets
                            if (
                                _SUBJECT_PREFIX_RE.sub('', t.title).strip().lower() == bare_incoming
                                and t.requester_email.lower() == sender_email.lower()
                            )
                        ]
                        if len(matches) == 1:
                            existing_ticket = matches[0]
                            logger.info(
                                f'[EmailPoller] Subject fallback matched ticket #{existing_ticket.pk} '
                                f'by normalized subject "{bare_incoming}" (sender: {sender_email})'
                            )

                if existing_ticket:
                    ticket = _handle_ticket_reply(msg, existing_ticket, client, mailbox)
                else:
                    ticket = _create_ticket_from_message(msg, client, mailbox)

                # Create EmailLog inside the same transaction so a crash between
                # ticket creation and log creation cannot produce duplicate tickets.
                # Use error='autoreply-discarded' when ticket is None (autoreply was
                # dropped) so the log is NOT mistaken for a deleted-ticket entry on
                # the next poll cycle (which would delete the log and re-process).
                EmailLog.objects.create(
                    message_id=internet_msg_id,
                    ticket=ticket,
                    error='' if ticket is not None else 'autoreply-discarded',
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


def _handle_ticket_reply(msg, ticket, client, mailbox):
    """
    Process an incoming email that is a reply to an existing ticket.
    Updates ticket status to 'user_responded' and logs the email in TicketEmail.
    Returns the Ticket instance.
    """
    from tickets.models import Ticket, TicketEmail, TicketAttachment

    sender = msg.get('from', {}).get('emailAddress', {})
    sender_email = sender.get('address', 'unknown@unknown.com')

    # Silently discard auto-replies (OOF, vacation notices, etc.) —
    # they must not reopen tickets or pollute the correspondence log.
    if _is_autoreply(msg):
        logger.info(
            '[EmailPoller] Ticket #%s — ignored auto-reply from %s',
            ticket.pk, sender_email,
        )
        return ticket

    body_content = msg.get('body', {}).get('content', '')
    content_type = msg.get('body', {}).get('contentType', '').lower()
    is_html = content_type == 'html'

    # Download attachments and replace cid: refs on the RAW body BEFORE sanitization.
    # nh3 may strip cid: src attributes even when 'cid' is in url_schemes; replacing
    # them with /attachments/ paths (no URL scheme) ensures they survive the sanitizer.
    cid_map = {}
    if is_html and (msg.get('hasAttachments') or 'cid:' in body_content):
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
                    if att_content_id:
                        cid_key = att_content_id.strip('<>').strip()
                        if cid_key:
                            cid_map[cid_key] = f'/attachments/{ta.pk}/download/?inline=1'
                finally:
                    os.unlink(tmp_path)
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not save reply attachments for ticket #{ticket.pk}: {exc}')

    if is_html and cid_map:
        for cid, url in cid_map.items():
            body_content = body_content.replace(f'cid:{cid}', url)

    if is_html:
        body_content = _sanitize_html(body_content)
        body_content = _strip_quoted_html(body_content)
    else:
        body_content = _strip_quoted_reply(body_content)

    subject = msg.get('subject', f'Re: [Ticket #{ticket.pk:04d}]').strip()

    # If the sender is an admin who is NOT the ticket's requester, log as internal note.
    # If the admin IS the requester (e.g. they sent a ticket to themselves for testing),
    # fall through to the normal user-reply path so the ticket flips to user_responded.
    from users.models import User
    try:
        sender_admin = User.objects.get(email__iexact=sender_email, is_admin=True)
    except User.DoesNotExist:
        sender_admin = None

    is_own_ticket = sender_email.lower() == (ticket.requester_email or '').lower()
    if sender_admin and not is_own_ticket:
        from tickets.models import TicketComment
        plain_body = _html_to_plain(body_content) if is_html else body_content
        TicketComment.objects.create(
            ticket=ticket,
            author=sender_admin,
            body=plain_body,
            is_internal=True,
        )
        if ticket.status not in Ticket.TERMINAL_STATUSES:
            ticket.status = Ticket.STATUS_USER_RESPONDED
            ticket.save(update_fields=['status', 'updated_at'])
        logger.info(f'[EmailPoller] Admin reply from {sender_email} added as internal note to ticket #{ticket.pk}')
        return ticket

    # Regular end-user reply — log in correspondence record (preserve HTML for inline images).
    email_to = _fmt_recipients(msg.get('toRecipients', []))
    email_cc = _fmt_recipients(msg.get('ccRecipients', []))
    TicketEmail.objects.create(
        ticket=ticket,
        direction=TicketEmail.DIRECTION_RECEIVED,
        subject=subject,
        body=body_content,
        body_is_html=is_html,
        from_email=sender_email,
        to_email=email_to,
        cc_emails=email_cc,
    )

    if ticket.status != Ticket.STATUS_USER_RESPONDED:
        ticket.status = Ticket.STATUS_USER_RESPONDED
        ticket.save(update_fields=['status'])
        logger.info(f'[EmailPoller] Ticket #{ticket.pk} marked as user_responded (reply from {sender_email})')

    try:
        from tasks.scheduled import send_assignee_user_replied
        send_assignee_user_replied.delay(ticket.pk)
    except Exception as exc:
        logger.warning(f'[EmailPoller] Could not queue assignee reply notification for ticket #{ticket.pk}: {exc}')

    return ticket


def _create_ticket_from_message(msg, client, mailbox):
    from tickets.models import Ticket, TicketAttachment

    sender = msg.get('from', {}).get('emailAddress', {})
    requester_email = sender.get('address', 'unknown@unknown.com')
    requester_name = sender.get('name', '')

    email_from = (f'{requester_name} <{requester_email}>' if requester_name and requester_name.lower() != requester_email.lower() else requester_email)
    email_to   = _fmt_recipients(msg.get('toRecipients', []))
    email_cc   = _fmt_recipients(msg.get('ccRecipients', []))

    if _is_autoreply(msg):
        logger.info('[EmailPoller] Discarded auto-reply new-ticket attempt from %s', requester_email)
        return None

    subject = msg.get('subject', '(No Subject)').strip() or '(No Subject)'

    body_content = msg.get('body', {}).get('content', '')
    content_type = msg.get('body', {}).get('contentType', '').lower()
    is_html = content_type == 'html'

    if is_html:
        body_content = _sanitize_html(body_content)

    from tickets.views import _set_default_category
    from users.models import User as UserModel
    try:
        requester = UserModel.objects.get(email__iexact=requester_email)
        requester_department = requester.department
    except UserModel.DoesNotExist:
        requester_department = ''

    ticket = Ticket(
        title=subject,
        description=body_content,
        description_is_html=is_html,
        requester_email=requester_email,
        requester_name=requester_name,
        requester_department=requester_department,
        source=Ticket.SOURCE_EMAIL,
        email_message_id=msg.get('internetMessageId', msg['id']),
        email_conversation_id=msg.get('conversationId', ''),
        email_from=email_from,
        email_to=email_to,
        email_cc=email_cc,
    )
    _set_default_category(ticket)
    ticket.save()

    # Download attachments; track inline CID → URL mapping for image resolution.
    # NOTE: Graph sets hasAttachments=False for inline-only images (e.g. signature logos),
    # so we also enter this block when cid: references exist in the HTML body.
    cid_map = {}
    has_inline_refs = is_html and 'cid:' in body_content
    if msg.get('hasAttachments') or has_inline_refs:
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
                    # Map any attachment that has a contentId, regardless of the isInline
                    # flag — Graph sometimes returns isInline=False for embedded images.
                    # Normalize by stripping angle brackets (some clients wrap contentId in <>).
                    if att_content_id:
                        cid_key = att_content_id.strip('<>').strip()
                        if cid_key:
                            cid_map[cid_key] = f'/attachments/{ta.pk}/download/?inline=1'
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

    # Detect HiBob new-employee notification → create a ProvisioningRequest
    _ticket_pk = ticket.pk
    _hibob_provisioning_data = None
    try:
        from hibob_sync.provisioning_utils import (
            is_hibob_new_employee_email, parse_hibob_email_body,
            resolve_m365_groups, UNIVERSAL_M365_GROUPS,
        )
        if is_hibob_new_employee_email(msg):
            raw_body = msg.get('body', {}).get('content', '')
            raw_ct   = msg.get('body', {}).get('contentType', '').lower()
            emp = parse_hibob_email_body(raw_body, is_html=(raw_ct == 'html'))
            if emp.get('first_name') and emp.get('last_name'):
                lookup_groups, fallback = resolve_m365_groups(
                    emp['region'], emp['country'], emp['division'], emp['department'],
                )
                all_groups = UNIVERSAL_M365_GROUPS + lookup_groups
                _hibob_provisioning_data = {**emp, 'm365_groups': all_groups, 'groups_fallback': fallback}
                logger.info(
                    '[EmailPoller] HiBob new-employee email detected for %s %s',
                    emp['first_name'], emp['last_name'],
                )
    except Exception as exc:
        logger.warning('[EmailPoller] HiBob provisioning parse failed: %s', exc)

    # Detect HiBob termination email → create an OffboardingRequest
    _hibob_offboarding_data = None
    try:
        from hibob_sync.provisioning_utils import (
            is_hibob_termination_email, parse_hibob_termination_body,
            parse_hibob_termination_subject, get_offboarding_scheduled_for,
        )
        if is_hibob_termination_email(msg):
            raw_body = msg.get('body', {}).get('content', '')
            raw_ct   = msg.get('body', {}).get('contentType', '').lower()
            term = parse_hibob_termination_body(raw_body, is_html=(raw_ct == 'html'))
            emp_name = parse_hibob_termination_subject(subject)
            if term.get('employee_email'):
                scheduled_for = None
                if term.get('termination_date') and term.get('country_origin'):
                    try:
                        scheduled_for = get_offboarding_scheduled_for(
                            term['termination_date'], term['country_origin'],
                        )
                    except Exception as tz_exc:
                        logger.warning('[EmailPoller] Could not compute scheduled_for: %s', tz_exc)
                _hibob_offboarding_data = {**term, 'employee_name': emp_name, 'scheduled_for': scheduled_for}
                logger.info(
                    '[EmailPoller] HiBob termination email detected for %s (%s)',
                    term.get('employee_email'), emp_name,
                )
    except Exception as exc:
        logger.warning('[EmailPoller] HiBob termination parse failed: %s', exc)

    def _dispatch_post_create_tasks():
        try:
            from tasks.scheduled import send_requester_created, generate_ai_summary
            send_requester_created.delay(_ticket_pk)
            generate_ai_summary.delay(_ticket_pk)
        except Exception as exc:
            logger.warning(f'[EmailPoller] Could not queue post-create tasks for ticket #{_ticket_pk}: {exc}')

        if _hibob_provisioning_data:
            try:
                from hibob_sync.models import ProvisioningRequest
                d = _hibob_provisioning_data
                ProvisioningRequest.objects.create(
                    ticket_id=_ticket_pk,
                    first_name=d['first_name'],
                    last_name=d['last_name'],
                    middle_name=d.get('middle_name', ''),
                    department=d['department'],
                    division=d['division'],
                    country=d['country'],
                    region=d['region'],
                    start_date=d.get('start_date'),
                    personal_mobile=d.get('personal_mobile', ''),
                    reports_to=d.get('reports_to', ''),
                    job_title=d.get('job_title', ''),
                    employment_type=d.get('employment_type', ''),
                    employee_id=d.get('employee_id', ''),
                    m365_groups=d['m365_groups'],
                    groups_fallback=d['groups_fallback'],
                    create_priority_ticket=d.get('create_priority_ticket', False),
                    priority_permissions_as=d.get('priority_permissions_as', ''),
                    create_salesforce_ticket=d.get('create_salesforce_ticket', False),
                    salesforce_country_permission=d.get('salesforce_country_permission', ''),
                    salesforce_permissions_as=d.get('salesforce_permissions_as', ''),
                    status='pending',
                )
                logger.info(
                    '[EmailPoller] ProvisioningRequest created for %s %s (ticket #%s)',
                    d['first_name'], d['last_name'], _ticket_pk,
                )
            except Exception as exc:
                logger.error('[EmailPoller] Failed to create ProvisioningRequest: %s', exc)

        if _hibob_offboarding_data:
            try:
                from hibob_sync.models import OffboardingRequest
                d = _hibob_offboarding_data
                OffboardingRequest.objects.create(
                    ticket_id=_ticket_pk,
                    employee_email=d['employee_email'],
                    employee_name=d.get('employee_name', ''),
                    department=d.get('department', ''),
                    direct_manager=d.get('direct_manager', ''),
                    country_origin=d.get('country_origin', ''),
                    termination_date=d.get('termination_date'),
                    termination_status=d.get('termination_status', ''),
                    scheduled_for=d.get('scheduled_for'),
                    status='pending',
                )
                logger.info(
                    '[EmailPoller] OffboardingRequest created for %s (ticket #%s)',
                    d['employee_email'], _ticket_pk,
                )
            except Exception as exc:
                logger.error('[EmailPoller] Failed to create OffboardingRequest: %s', exc)

    # Use on_commit so tasks are only dispatched after the enclosing transaction
    # commits — dispatching inside an uncommitted atomic() risks queuing a task
    # whose ticket pk either no longer exists (after a rollback) or resolves to a
    # stale committed row with the same pk, causing wrong-ticket confirmation emails.
    transaction.on_commit(_dispatch_post_create_tasks)

    logger.info(f'[EmailPoller] Created ticket #{_ticket_pk} from email: {subject}')
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


def _strip_quoted_html(html: str) -> str:
    """
    Remove quoted reply content from HTML before plain-text conversion.
    Handles Outlook <hr> reply dividers, <blockquote> elements, and signature divs.
    """
    # Outlook inserts <hr> as a visual reply divider — everything after is the original
    hr_match = re.search(r'<hr\b[^>]*/?>|<hr\b[^>]*></hr>', html, re.IGNORECASE)
    if hr_match:
        html = html[:hr_match.start()]

    # Strip from the first <blockquote> — everything inside is quoted context
    bq_match = re.search(r'<blockquote\b', html, re.IGNORECASE)
    if bq_match:
        html = html[:bq_match.start()]

    # Remove Outlook Web / Exchange signature div (id="Signature" or class="*signature*")
    html = re.sub(
        r'<div[^>]+(?:id|class)=["\'][^"\']*signature[^"\']*["\'][^>]*>.*',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return html


def _strip_quoted_reply(text: str) -> str:
    """Remove the quoted original message and signature from a reply body."""
    separators = [
        r'^_{3,}$',                          # ___ divider line
        r'^-{3,}$',                          # --- divider line
        r'^From:\s',                         # Outlook reply header (on its own line)
        r'^Sent:\s',                         # Outlook reply header (on its own line)
        r'^On .+ wrote:',                    # Gmail/Apple "On ... wrote:"
        r'^\s*>',                            # Quoted lines starting with >
        r'^--\s*$',                          # Standard email signature separator
    ]
    pattern = re.compile('|'.join(separators), re.IGNORECASE)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            text = '\n'.join(lines[:i]).strip()
            break

    # Truncate at 3+ consecutive blank lines — signature heuristic for plain-text emails
    blank_run = re.search(r'\n{3,}', text)
    if blank_run:
        text = text[:blank_run.start()].strip()

    return text


def _sanitize_html(html: str) -> str:
    """
    Strip dangerous elements from email HTML using nh3 allowlist sanitizer.
    Extracts body content and preserves safe formatting and inline images.
    """
    import re
    import nh3

    # Extract just the <body> content if this is a full HTML document
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        html = body_match.group(1)

    ALLOWED_TAGS = {
        'a', 'b', 'i', 'strong', 'em', 'u', 's', 'br', 'p', 'div', 'span',
        'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'hr', 'pre', 'code', 'blockquote',
        'table', 'thead', 'tbody', 'tr', 'td', 'th',
        'img',
    }
    ALLOWED_ATTRS = {
        'a':     {'href', 'title', 'target'},
        'img':   {'src', 'alt', 'width', 'height', 'style'},
        'td':    {'colspan', 'rowspan', 'align', 'style'},
        'th':    {'colspan', 'rowspan', 'align', 'style'},
        'div':   {'style'},
        'span':  {'style'},
        'p':     {'style'},
        'table': {'style', 'border', 'cellpadding', 'cellspacing'},
    }

    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={'http', 'https', 'mailto', 'cid'},
        link_rel=None,
    )

    # Strip img tags with external src (http/https) — they cause browser fetches that
    # can freeze the ticket detail page and leak read-receipts to the sender's server.
    # Only cid: refs (resolved to local /attachments/ URLs) and own-domain /... paths are kept.
    cleaned = re.sub(r'<img[^>]+src=["\']https?://[^"\']*["\'][^>]*/?>', '', cleaned, flags=re.IGNORECASE)

    return cleaned.strip()
