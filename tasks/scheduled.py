"""
Celery tasks — run in the background on a schedule.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Email polling ─────────────────────────────────────────────────────────────

@shared_task(name='tasks.poll_mailbox')
def poll_mailbox():
    """Check the servicedesk mailbox for new emails and create tickets."""
    from integrations.email_poller import poll_mailbox as _poll
    logger.info('[Task] poll_mailbox started')
    _poll()
    logger.info('[Task] poll_mailbox finished')


# ── User sync ─────────────────────────────────────────────────────────────────

@shared_task(name='tasks.sync_users')
def sync_users():
    """Sync users from the KramerLicensedUsers Entra group."""
    from integrations.user_sync import sync_users as _sync
    logger.info('[Task] sync_users started')
    _sync()
    logger.info('[Task] sync_users finished')


@shared_task(name='tasks.sync_admins')
def sync_admins():
    """Sync admin users from the Global_OPS_IT Entra group."""
    from integrations.user_sync import sync_admins as _sync
    logger.info('[Task] sync_admins started')
    _sync()
    logger.info('[Task] sync_admins finished')


# ── SLA checks ────────────────────────────────────────────────────────────────

@shared_task(name='tasks.check_sla')
def check_sla():
    """
    Mark tickets as SLA-breached and send notification emails
    when the SLA deadline has passed.
    Skips entirely when SLA is globally suspended.
    """
    from tickets.models import Ticket, SystemSetting
    if SystemSetting.get('sla_paused', '0') == '1':
        logger.info('[SLA] Check skipped — SLA is suspended.')
        return
    now = timezone.now()

    # Find tickets that just breached (deadline passed but not yet flagged)
    newly_breached = Ticket.objects.filter(
        sla_deadline__lte=now,
        sla_breached=False,
    ).exclude(status__in=Ticket.TERMINAL_STATUSES)

    for ticket in newly_breached:
        ticket.sla_breached = True
        ticket.save(update_fields=['sla_breached'])
        logger.info(f'[SLA] Ticket #{ticket.pk} breached SLA.')

        # Notify assignee
        if ticket.assignee and ticket.assignee.notify_on_sla_breach:
            _send_sla_breach_email(ticket)

    # Find tickets at 75%+ of their SLA window (warning zone) — send warning once
    # We use the sla_warning_sent flag pattern via SystemSetting key
    warning_tickets = Ticket.objects.filter(
        sla_deadline__isnull=False,
        sla_breached=False,
    ).exclude(status__in=Ticket.TERMINAL_STATUSES)

    for ticket in warning_tickets:
        if ticket.sla_percent_elapsed >= 75:
            cache_key = f'sla_warning_sent_{ticket.pk}'
            from tickets.models import SystemSetting
            if not SystemSetting.objects.filter(key=cache_key).exists():
                SystemSetting.set(cache_key, '1')
                if ticket.assignee and ticket.assignee.notify_on_sla_breach:
                    _send_sla_warning_email(ticket)
                    logger.info(f'[SLA] Warning sent for ticket #{ticket.pk}.')


def _email_html(header_title: str, header_subtitle: str, greeting: str, body_rows: str,
                cta_url: str = None, cta_label: str = None, header_color: str = '#8200B4') -> str:
    """
    Render a fully branded Kramer email.
    body_rows: HTML rows for the details table (tr elements).
    """
    logo_url = f'{settings.SITE_URL}/static/img/kramer_logo.png'
    cta_block = ''
    if cta_url and cta_label:
        cta_block = f'''
        <tr><td style="padding:24px 0 8px;">
          <a href="{cta_url}"
             style="display:inline-block;padding:12px 28px;background:{header_color};
                    color:#ffffff;text-decoration:none;border-radius:6px;
                    font-weight:600;font-size:14px;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
            {cta_label}
          </a>
        </td></tr>'''

    details_block = ''
    if body_rows:
        details_block = f'''
        <tr><td>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f8f0ff;border-left:4px solid {header_color};border-radius:4px;">
            <tr><td style="padding:18px 22px;">
              <table width="100%" cellpadding="5" cellspacing="0"
                     style="font-size:14px;color:#333333;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
                {body_rows}
              </table>
            </td></tr>
          </table>
        </td></tr>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f0;padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.10);">

  <!-- Header -->
  <tr>
    <td style="background:{header_color};padding:28px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="vertical-align:middle;">
            <p style="margin:0;color:#ffffff;font-size:11px;letter-spacing:2px;
                      text-transform:uppercase;opacity:0.75;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
              IT Support
            </p>
            <h1 style="margin:4px 0 0;color:#ffffff;font-size:20px;font-weight:700;
                       font-family:'Segoe UI',Calibri,Arial,sans-serif;line-height:1.3;">
              {header_title}
            </h1>
            {f'<p style="margin:4px 0 0;color:rgba(255,255,255,0.82);font-size:13px;font-family:Segoe UI,Calibri,Arial,sans-serif;">{header_subtitle}</p>' if header_subtitle else ''}
          </td>
          <td style="text-align:right;vertical-align:middle;padding-left:16px;">
            <img src="{logo_url}" alt="Kramer" width="110" height="auto"
                 style="display:block;max-width:110px;filter:brightness(0) invert(1);opacity:0.9;">
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td style="padding:32px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="font-size:15px;color:#333333;padding-bottom:20px;
                       font-family:'Segoe UI',Calibri,Arial,sans-serif;line-height:1.6;">
          {greeting}
        </td></tr>
        {details_block}
        {cta_block}
      </table>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background:#1a1a2e;padding:22px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="color:#aaaaaa;font-size:12px;line-height:1.7;
                     font-family:'Segoe UI',Calibri,Arial,sans-serif;">
            <strong style="color:#cccccc;">IT Support Team</strong><br>
            <a href="mailto:servicedesk@kramerav.com"
               style="color:#cc66ff;text-decoration:none;">servicedesk@kramerav.com</a>
          </td>
          <td style="text-align:right;vertical-align:middle;">
            <span style="color:#444466;font-size:18px;font-weight:700;
                         font-family:'Segoe UI',Calibri,Arial,sans-serif;letter-spacing:1px;">
              KRAMER
            </span>
          </td>
        </tr>
      </table>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>'''


def _row(label: str, value: str, color: str = '#8200B4') -> str:
    return (f'<tr>'
            f'<td style="color:{color};font-weight:600;white-space:nowrap;width:140px;'
            f'    vertical-align:top;padding:4px 16px 4px 0;">{label}</td>'
            f'<td style="color:#333333;vertical-align:top;padding:4px 0;">{value}</td>'
            f'</tr>')


def _send_sla_breach_email(ticket):
    name = ticket.assignee.display_name or ticket.assignee.email
    deadline = ticket.sla_deadline.strftime('%d %b %Y %H:%M') if ticket.sla_deadline else 'N/A'
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='SLA Deadline Breached',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {ticket.title}',
        header_color='#c0392b',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'The following ticket has <strong>breached its SLA deadline</strong> '
                  f'and requires your immediate attention.'),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}', '#c0392b') +
            _row('Requester', f'{ticket.requester_name} ({ticket.requester_email})', '#c0392b') +
            _row('SLA Deadline', deadline, '#c0392b')
        ),
        cta_url=ticket_url,
        cta_label='Open Ticket',
    )
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Kdesk] SLA Breached — #{ticket.pk:04d}: {ticket.title}',
        body=body,
    )


def _send_sla_warning_email(ticket):
    name = ticket.assignee.display_name or ticket.assignee.email
    deadline = ticket.sla_deadline.strftime('%d %b %Y %H:%M') if ticket.sla_deadline else 'N/A'
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='SLA Warning',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {ticket.title}',
        header_color='#e67e22',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'This ticket is at <strong>{ticket.sla_percent_elapsed}% of its SLA window</strong>. '
                  f'Please respond soon to avoid a breach.'),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}', '#e67e22') +
            _row('SLA Deadline', deadline, '#e67e22') +
            _row('Elapsed', f'{ticket.sla_percent_elapsed}%', '#e67e22')
        ),
        cta_url=ticket_url,
        cta_label='Open Ticket',
    )
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Kdesk] SLA Warning — #{ticket.pk:04d}: {ticket.title}',
        body=body,
    )


# ── Requester emails ──────────────────────────────────────────────────────────

@shared_task(name='tasks.send_requester_created')
def send_requester_created(ticket_pk: int):
    """Email the requester confirming their ticket was received."""
    from tickets.models import Ticket
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return
    name = ticket.requester_name or ticket.requester_email
    submitted = ticket.created_at.strftime('%d %b %Y %H:%M') if ticket.created_at else 'N/A'
    body = _email_html(
        header_title='We received your request',
        header_subtitle=f'Ticket #{ticket.pk:04d}',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'Your support request has been received and logged. '
                  f'Our IT team will look into it and get back to you as soon as possible.'),
        body_rows=(
            _row('Ticket #', f'#{ticket.pk:04d}') +
            _row('Subject', ticket.title) +
            _row('Submitted', submitted)
        ),
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Ticket #{ticket.pk:04d}] Your request has been received',
        body=body,
    )
    logger.info(f'[Requester] Creation confirmation sent for ticket #{ticket_pk}.')


@shared_task(name='tasks.send_requester_closed')
def send_requester_closed(ticket_pk: int):
    """Email the requester when their ticket is closed (if the global toggle is on)."""
    from tickets.models import Ticket, SystemSetting
    if SystemSetting.get('notify_requester_on_close', '1') == '0':
        logger.info(f'[Requester] Close notification suppressed (global toggle off) for ticket #{ticket_pk}.')
        return
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return
    name = ticket.requester_name or ticket.requester_email
    closed = ticket.resolved_at.strftime('%d %b %Y %H:%M') if ticket.resolved_at else 'N/A'
    solution_row = _row('Resolution', ticket.solution) if ticket.solution else ''
    body = _email_html(
        header_title='Your ticket has been closed',
        header_subtitle=f'Ticket #{ticket.pk:04d}',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'Your support ticket has been resolved and closed. '
                  f'If you need further assistance, please don\'t hesitate to reach out.'),
        body_rows=(
            _row('Ticket #', f'#{ticket.pk:04d}') +
            _row('Subject', ticket.title) +
            _row('Closed', closed) +
            solution_row
        ),
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Ticket #{ticket.pk:04d}] Your ticket has been closed',
        body=body,
    )
    logger.info(f'[Requester] Close notification sent for ticket #{ticket_pk}.')


# ── Notification emails ───────────────────────────────────────────────────────

@shared_task(name='tasks.send_ticket_notification')
def send_ticket_notification(event_type: str, ticket_pk: int, actor_pk):
    """
    Send an email notification for ticket events.
    event_type: 'assign' | 'update'
    """
    from tickets.models import Ticket
    from users.models import User

    try:
        ticket = Ticket.objects.select_related('assignee').get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return

    actor = None
    if actor_pk:
        try:
            actor = User.objects.get(pk=actor_pk)
        except User.DoesNotExist:
            pass

    actor_name = str(actor) if actor else 'System'
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'

    if event_type == 'assign' and ticket.assignee:
        name = ticket.assignee.display_name or ticket.assignee.email
        deadline = ticket.sla_deadline.strftime('%d %b %Y %H:%M') if ticket.sla_deadline else 'N/A'
        body = _email_html(
            header_title='Ticket Assigned to You',
            header_subtitle=f'#{ticket.pk:04d} — {ticket.title}',
            greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                      f'A support ticket has been assigned to you.'),
            body_rows=(
                _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}') +
                _row('Requester', f'{ticket.requester_name} ({ticket.requester_email})') +
                _row('SLA Deadline', deadline)
            ),
            cta_url=ticket_url,
            cta_label='Open Ticket',
        )
        _send_notification_email(
            to=ticket.assignee.email,
            subject=f'[Kdesk] Ticket Assigned — #{ticket.pk:04d}: {ticket.title}',
            body=body,
        )

    elif event_type == 'update' and ticket.assignee:
        name = ticket.assignee.display_name or ticket.assignee.email
        body = _email_html(
            header_title='Ticket Updated',
            header_subtitle=f'#{ticket.pk:04d} — {ticket.title}',
            greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                      f'Ticket <strong>#{ticket.pk:04d}</strong> was updated by <strong>{actor_name}</strong>.'),
            body_rows=(
                _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}') +
                _row('Status', ticket.get_status_display()) +
                _row('Updated by', actor_name)
            ),
            cta_url=ticket_url,
            cta_label='Open Ticket',
        )
        _send_notification_email(
            to=ticket.assignee.email,
            subject=f'[Kdesk] Ticket Updated — #{ticket.pk:04d}: {ticket.title}',
            body=body,
        )


def _send_maintenance_announcement(change):
    """Send a Planned Maintenance broadcast email to all affected employees."""
    import os
    from changes.models import Change

    # Determine recipient list
    region_recipients = {
        Change.REGION_ISRAEL: 'IL_All_Employees@kramerav.com',
        Change.REGION_GLOBAL: 'GLOBAL_All_Employees@kramerav.com',
    }
    to_email = region_recipients.get(change.affected_region)
    if not to_email:
        logger.warning(f'[Change] Unknown region "{change.affected_region}" — skipping broadcast.')
        return

    # Format date and timeframe
    if change.planned_date:
        date_str = change.planned_date.strftime('%A, %d %B %Y')
    else:
        date_str = 'TBD'

    if change.planned_from and change.planned_to:
        timeframe_str = f'{change.planned_from.strftime("%H:%M")} – {change.planned_to.strftime("%H:%M")}'
    elif change.planned_from:
        timeframe_str = f'From {change.planned_from.strftime("%H:%M")}'
    else:
        timeframe_str = 'To be confirmed'

    system_str = change.affected_system_display
    region_str = change.get_affected_region_display()

    body = _email_html(
        header_title='Planned Maintenance Notification',
        header_subtitle=f'{system_str} — {date_str}',
        greeting=(
            'Dear Employees,<br><br>'
            'Please be informed that the IT Department has scheduled a <strong>Planned Maintenance</strong> '
            'window. During this time, the affected system may be temporarily unavailable.<br><br>'
            'We apologize for any inconvenience and will work to minimize disruption. '
            'If you have any questions please contact '
            '<a href="mailto:servicedesk@kramerav.com" style="color:#8200B4;">servicedesk@kramerav.com</a>.'
        ),
        body_rows=(
            _row('System', system_str) +
            _row('Date', date_str) +
            _row('Timeframe', timeframe_str) +
            _row('Region', region_str)
        ),
    )

    subject = f'[Planned Maintenance] {system_str} – {date_str}, {timeframe_str}'
    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=settings.SERVICEDESK_EMAIL,  # TO: servicedesk itself
            bcc_email=to_email,                   # BCC: the distribution group
            subject=subject,
            body_html=body,
        )
        logger.info(f'[Change] Maintenance announcement sent (BCC) to {to_email} for change #{change.pk}.')
    except Exception as exc:
        logger.error(f'[Change] Failed to send maintenance announcement: {exc}')


def _send_notification_email(to: str, subject: str, body: str):
    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=to,
            subject=subject,
            body_html=body,
        )
    except Exception as exc:
        logger.error(f'[Notification] Failed to send email to {to}: {exc}')


# ── AI Summary ───────────────────────────────────────────────────────────────

@shared_task(name='tasks.generate_ai_summary')
def generate_ai_summary(ticket_pk: int):
    """Generate a one-sentence AI summary for a ticket using Claude Haiku."""
    from tickets.models import Ticket
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return

    if not settings.GROQ_API_KEY:
        logger.warning('[AISummary] GROQ_API_KEY not set — skipping.')
        return

    # Build a clean plain-text excerpt (strip HTML tags if needed)
    description = ticket.description or ''
    if ticket.description_is_html:
        import re
        description = re.sub(r'<[^>]+>', ' ', description)
        description = re.sub(r'\s+', ' ', description).strip()

    excerpt = description[:800]

    prompt = (
        f'You are an IT helpdesk assistant. Write ONE short sentence (max 15 words) '
        f'summarising what the following IT support ticket is about. '
        f'Start with the requester\'s name if known. Do not use quotes. '
        f'Examples: "David is requesting help with a Priority error." '
        f'"Nofar is asking for a new mouse."\n\n'
        f'Requester: {ticket.requester_name or ticket.requester_email}\n'
        f'Subject: {ticket.title}\n'
        f'Description: {excerpt}'
    )

    try:
        from groq import Groq
        client = Groq(api_key=settings.GROQ_API_KEY)
        response = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            max_tokens=60,
            messages=[{'role': 'user', 'content': prompt}],
        )
        summary = response.choices[0].message.content.strip()
        ticket.ai_summary = summary
        ticket.save(update_fields=['ai_summary'])
        logger.info(f'[AISummary] Generated summary for ticket #{ticket_pk}.')
    except Exception as exc:
        logger.error(f'[AISummary] Failed for ticket #{ticket_pk}: {exc}')


# ── Change Management notifications ──────────────────────────────────────────

def _get_it_manager_emails():
    """Return email addresses of all current IT managers from the DB."""
    from users.models import User
    return list(User.objects.filter(is_it_manager=True, is_active=True).values_list('email', flat=True))


@shared_task(name='tasks.notify_change')
def notify_change(change_pk: int, event: str):
    """
    Send email notifications for change lifecycle events.
    event: 'submitted' | 'approved' | 'done'
    """
    from changes.models import Change
    try:
        change = Change.objects.select_related('submitted_by').get(pk=change_pk)
    except Change.DoesNotExist:
        return

    submitter_email = change.submitted_by.email if change.submitted_by else None
    submitter_name = (
        change.submitted_by.display_name or change.submitted_by.email
        if change.submitted_by else 'IT Team'
    )
    if change.planned_date:
        planned = change.planned_date.strftime('%Y-%m-%d')
        if change.planned_from and change.planned_to:
            planned += f' {change.planned_from.strftime("%H:%M")} – {change.planned_to.strftime("%H:%M")}'
        elif change.planned_from:
            planned += f' from {change.planned_from.strftime("%H:%M")}'
    else:
        planned = 'N/A'

    change_url = f'{settings.SITE_URL}/changes/{change.pk}/'

    change_rows = (
        _row('Change', f'#{change.pk:04d} — {change.title}') +
        _row('Risk Level', change.get_risk_level_display()) +
        _row('Affected System', change.affected_system_display) +
        _row('Planned Date', planned) +
        _row('Submitted By', submitter_name)
    )

    it_manager_emails = _get_it_manager_emails()

    if event == 'submitted':
        subject = f'[Kdesk] Change Request Pending Approval — #{change.pk:04d}: {change.title}'
        body = _email_html(
            header_title='Change Request Pending Approval',
            header_subtitle=f'#{change.pk:04d} — {change.title}',
            greeting='A new change request has been submitted and is awaiting your approval.',
            body_rows=change_rows,
            cta_url=change_url,
            cta_label='Review &amp; Approve in Kdesk',
        )
        for mgr_email in it_manager_emails:
            _send_notification_email(to=mgr_email, subject=subject, body=body)
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Submitted — #{change.pk:04d}: {change.title}',
                body=_email_html(
                    header_title='Change Request Submitted',
                    header_subtitle=f'#{change.pk:04d} — {change.title}',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Your change request has been submitted and is now pending approval '
                              f'by the IT Manager. You will be notified once it is approved.'),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}') +
                        _row('Planned Date', planned)
                    ),
                    cta_url=change_url,
                    cta_label='View in Kdesk',
                ),
            )

    elif event == 'approved':
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Approved — #{change.pk:04d}: {change.title}',
                body=_email_html(
                    header_title='Change Request Approved',
                    header_subtitle=f'#{change.pk:04d} — {change.title}',
                    header_color='#1a7a4a',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Your change request has been <strong>approved</strong>. '
                              f'You may now proceed with implementation.'),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}', '#1a7a4a') +
                        _row('Planned Date', planned, '#1a7a4a')
                    ),
                    cta_url=change_url,
                    cta_label='View in Kdesk',
                ),
            )
        # Broadcast maintenance announcement to all affected employees
        _send_maintenance_announcement(change)

    elif event == 'not_approved':
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Not Approved — #{change.pk:04d}: {change.title}',
                body=_email_html(
                    header_title='Change Request Not Approved',
                    header_subtitle=f'#{change.pk:04d} — {change.title}',
                    header_color='#c0392b',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Your change request has been reviewed and was <strong>not approved</strong> '
                              f'at this time. Please reach out to the IT Manager for more information.'),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}', '#c0392b') +
                        _row('Planned Date', planned, '#c0392b')
                    ),
                    cta_url=change_url,
                    cta_label='View in Kdesk',
                ),
            )

    elif event == 'done':
        done_rows = (
            _row('Change', f'#{change.pk:04d} — {change.title}') +
            _row('Affected System', change.affected_system_display) +
            _row('Risk Level', change.get_risk_level_display()) +
            _row('Implemented By', submitter_name)
        )
        done_subject = f'[Kdesk] Change Completed — #{change.pk:04d}: {change.title}'
        done_body = _email_html(
            header_title='Change Completed',
            header_subtitle=f'#{change.pk:04d} — {change.title}',
            greeting='The following change has been completed.',
            body_rows=done_rows,
            cta_url=change_url,
            cta_label='View in Kdesk',
        )
        for mgr_email in it_manager_emails:
            _send_notification_email(to=mgr_email, subject=done_subject, body=done_body)
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=done_subject,
                body=_email_html(
                    header_title='Change Marked as Done',
                    header_subtitle=f'#{change.pk:04d} — {change.title}',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Change <strong>#{change.pk:04d} — {change.title}</strong> '
                              f'has been marked as <strong>Done</strong>. Well done!'),
                    body_rows=done_rows,
                ),
            )

    logger.info(f'[Change] Notification sent for change #{change_pk}, event={event}')


# ── Change status reminders ───────────────────────────────────────────────────

@shared_task(name='tasks.check_change_reminders')
def check_change_reminders():
    """
    Send email reminders to change submitters:
    - When planned_from arrives: remind to mark as In Progress
    - When planned_to arrives: remind to mark as Done
    Runs every 15 minutes alongside the SLA checker.
    """
    from datetime import datetime, date
    from changes.models import Change

    now = timezone.now()
    today = now.date()
    current_time = now.time()

    # ── Reminder 1: Start reminder ─────────────────────────────────────────────
    # Approved changes whose planned window has started but submitter hasn't moved them
    start_candidates = Change.objects.filter(
        status=Change.STATUS_APPROVED,
        planned_date__lte=today,
        planned_from__isnull=False,
        reminded_start=False,
    ).select_related('submitted_by')

    for change in start_candidates:
        # Only trigger once the planned_from time has passed on the planned date
        if change.planned_date < today or (change.planned_date == today and change.planned_from <= current_time):
            _send_change_reminder(change, 'start')
            change.reminded_start = True
            change.save(update_fields=['reminded_start'])
            logger.info(f'[Change] Start reminder sent for change #{change.pk}.')

    # ── Reminder 2: Done reminder ──────────────────────────────────────────────
    # In-progress changes whose planned window has ended
    done_candidates = Change.objects.filter(
        status=Change.STATUS_IN_PROGRESS,
        planned_date__lte=today,
        planned_to__isnull=False,
        reminded_done=False,
    ).select_related('submitted_by')

    for change in done_candidates:
        if change.planned_date < today or (change.planned_date == today and change.planned_to <= current_time):
            _send_change_reminder(change, 'done')
            change.reminded_done = True
            change.save(update_fields=['reminded_done'])
            logger.info(f'[Change] Done reminder sent for change #{change.pk}.')

    # ── Reminder 3: Done follow-up (1 hour after planned_to) ──────────────────
    # Any non-terminal change still not marked Done 1 hour after planned_to
    from datetime import datetime, timedelta
    followup_candidates = Change.objects.filter(
        planned_date__isnull=False,
        planned_to__isnull=False,
        reminded_done_followup=False,
    ).exclude(
        status__in=[Change.STATUS_DONE, Change.STATUS_CANCELLED],
    ).select_related('submitted_by')

    for change in followup_candidates:
        window_end = datetime.combine(change.planned_date, change.planned_to)
        window_end_aware = timezone.make_aware(window_end)
        if now >= window_end_aware + timedelta(hours=1):
            _send_change_reminder(change, 'done_followup')
            change.reminded_done_followup = True
            change.save(update_fields=['reminded_done_followup'])
            logger.info(f'[Change] Done follow-up reminder sent for change #{change.pk}.')


def _send_change_reminder(change, reminder_type: str):
    """Send a status-update reminder email to the change submitter."""
    if not change.submitted_by:
        return

    to_email = change.submitted_by.email
    submitter_name = change.submitted_by.display_name or change.submitted_by.email
    change_url = f'{settings.SITE_URL}/changes/{change.pk}/'

    if change.planned_from and change.planned_to:
        timeframe = f'{change.planned_from.strftime("%H:%M")} – {change.planned_to.strftime("%H:%M")}'
    elif change.planned_from:
        timeframe = f'From {change.planned_from.strftime("%H:%M")}'
    else:
        timeframe = 'N/A'

    date_str = change.planned_date.strftime('%A, %d %B %Y') if change.planned_date else 'N/A'

    detail_rows = (
        _row('Change', f'#{change.pk:04d} — {change.title}') +
        _row('System', change.affected_system_display) +
        _row('Date', date_str) +
        _row('Timeframe', timeframe)
    )

    if reminder_type == 'start':
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as In Progress'
        header_title = 'Action Needed — Mark as In Progress'
        action_label = 'Mark as In Progress'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'has started ({timeframe}). Please mark the change as <strong>In Progress</strong> '
            f'in Kdesk so the team knows the work has begun.'
        )
    elif reminder_type == 'done_followup':
        subject = f'[Kdesk] Action Required: Change #{change.pk:04d} Still Not Closed'
        header_title = 'Action Required — Change Not Yet Closed'
        action_label = 'Mark as Done'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'ended over an hour ago ({timeframe}), but the change has not been marked as '
            f'<strong>Done</strong> yet. Please update the status as soon as the work is complete.'
        )
    else:
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as Done'
        header_title = 'Action Needed — Mark as Done'
        action_label = 'Mark as Done'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'has ended ({timeframe}). Please mark the change as <strong>Done</strong> '
            f'in Kdesk once the work is complete.'
        )

    body = _email_html(
        header_title=header_title,
        header_subtitle=f'#{change.pk:04d} — {change.title}',
        greeting=greeting,
        body_rows=detail_rows,
        cta_url=change_url,
        cta_label=f'{action_label} in Kdesk',
    )
    _send_notification_email(to=to_email, subject=subject, body=body)


# ── Setup scheduled tasks in the DB ──────────────────────────────────────────

def register_periodic_tasks():
    """
    Called from a management command on first run to seed the Celery Beat schedule.
    """
    from django_celery_beat.models import PeriodicTask, IntervalSchedule

    poll_interval, _ = IntervalSchedule.objects.get_or_create(
        every=30,
        period=IntervalSchedule.SECONDS,
    )
    sync_interval, _ = IntervalSchedule.objects.get_or_create(every=60, period=IntervalSchedule.MINUTES)
    weekly_interval, _ = IntervalSchedule.objects.get_or_create(every=10080, period=IntervalSchedule.MINUTES)
    sla_interval, _ = IntervalSchedule.objects.get_or_create(every=15, period=IntervalSchedule.MINUTES)

    tasks = [
        ('Poll Mailbox', 'tasks.poll_mailbox', poll_interval),
        ('Sync Entra Users', 'tasks.sync_users', sync_interval),
        ('Sync Entra Admins', 'tasks.sync_admins', sync_interval),
        ('Check SLA', 'tasks.check_sla', sla_interval),
        ('Check Change Reminders', 'tasks.check_change_reminders', sla_interval),
    ]

    for name, task_name, schedule in tasks:
        PeriodicTask.objects.get_or_create(
            name=name,
            defaults={'task': task_name, 'interval': schedule, 'enabled': True},
        )
