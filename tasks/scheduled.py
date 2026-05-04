"""
Celery tasks — run in the background on a schedule.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.utils.html import escape as _esc

logger = logging.getLogger(__name__)


# ── Email polling ─────────────────────────────────────────────────────────────

@shared_task(name='tasks.poll_mailbox', time_limit=300, soft_time_limit=270)
def poll_mailbox():
    """Check the servicedesk mailbox for new emails and create tickets."""
    from tickets.models import SystemSetting
    if SystemSetting.get('emails_enabled', '1') != '1':
        logger.info('[Task] poll_mailbox skipped — emails disabled.')
        return
    from integrations.email_poller import poll_mailbox as _poll
    logger.info('[Task] poll_mailbox started')
    _poll()
    logger.info('[Task] poll_mailbox finished')


# ── User sync ─────────────────────────────────────────────────────────────────

@shared_task(name='tasks.sync_users', time_limit=300, soft_time_limit=270)
def sync_users():
    """Sync users from the KramerLicensedUsers Entra group."""
    from integrations.user_sync import sync_users as _sync
    logger.info('[Task] sync_users started')
    _sync()
    logger.info('[Task] sync_users finished')


@shared_task(name='tasks.sync_admins', time_limit=300, soft_time_limit=270)
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
        try:
            if ticket.sla_percent_elapsed >= 75:
                cache_key = f'sla_warning_sent_{ticket.pk}'
                from tickets.models import SystemSetting
                if not SystemSetting.objects.filter(key=cache_key).exists():
                    SystemSetting.set(cache_key, '1')
                    if ticket.assignee and ticket.assignee.notify_on_sla_breach:
                        _send_sla_warning_email(ticket)
                        logger.info(f'[SLA] Warning sent for ticket #{ticket.pk}.')
        except Exception as exc:
            logger.error(f'[SLA] Failed to process warning for ticket #{ticket.pk}: {exc}')


def _email_html(header_title: str, header_subtitle: str, greeting: str, body_rows: str,
                cta_url: str = None, cta_label: str = None,
                header_color: str = '#8205B4', header_text_color: str = '#ffffff') -> str:
    """
    Render a fully branded Kramer email.
    body_rows: HTML rows for the details table (tr elements).
    header_text_color: '#ffffff' for dark headers, '#1a1a2e' for light headers (e.g. green).
    """
    logo_url = f'{settings.SITE_URL}/static/img/kramer_logo.png'
    logo_footer_url = f'{settings.SITE_URL}/static/img/kramer_logo_footer.png'
    subtitle_opacity = '0.65' if header_text_color != '#ffffff' else '0.82'
    logo_filter = 'brightness(0)' if header_text_color != '#ffffff' else 'brightness(0) invert(1)'

    cta_block = ''
    if cta_url and cta_label:
        cta_block = f'''
        <tr><td style="padding:24px 0 8px;">
          <a href="{cta_url}"
             style="display:inline-block;padding:12px 28px;background:{header_color};
                    color:{header_text_color};text-decoration:none;border-radius:6px;
                    font-weight:600;font-size:14px;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
            {cta_label}
          </a>
        </td></tr>'''

    details_block = ''
    if body_rows:
        details_block = f'''
        <tr><td>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f5f5f5;border-left:4px solid {header_color};border-radius:4px;">
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
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <style>
    :root{{color-scheme:light only;}}
    [data-ogsc] .og-header{{background-color:{header_color}!important;}}
    [data-ogsc] .og-body{{background-color:#ffffff!important;color:#333333!important;}}
    [data-ogsc] .og-footer{{background-color:#1a1a2e!important;}}
    [data-ogsb] .og-header{{background-color:{header_color}!important;}}
    [data-ogsb] .og-body{{background-color:#ffffff!important;}}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f0f0f0;color-scheme:light;
             font-family:'Segoe UI',Calibri,Arial,sans-serif;" bgcolor="#f0f0f0">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f0f0f0"
       style="background-color:#f0f0f0;padding:30px 0;">
<tr><td align="center" style="padding:30px 0;">

<table width="600" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
       style="background-color:#ffffff;border-radius:10px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.10);width:600px;">

  <!-- Header -->
  <tr>
    <td class="og-header" bgcolor="{header_color}"
        style="background-color:{header_color};padding:28px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="vertical-align:middle;">
            <p style="margin:0;color:{header_text_color};font-size:11px;letter-spacing:2px;
                      text-transform:uppercase;opacity:{subtitle_opacity};font-family:'Segoe UI',Calibri,Arial,sans-serif;">
              IT Support
            </p>
            <h1 style="margin:4px 0 0;color:{header_text_color};font-size:20px;font-weight:700;
                       font-family:'Segoe UI',Calibri,Arial,sans-serif;line-height:1.3;">
              {header_title}
            </h1>
            {f'<p style="margin:4px 0 0;color:{header_text_color};opacity:{subtitle_opacity};font-size:13px;font-family:Segoe UI,Calibri,Arial,sans-serif;">{_esc(header_subtitle)}</p>' if header_subtitle else ''}
          </td>
          <td style="text-align:right;vertical-align:middle;padding-left:16px;">
            <img src="{logo_url}" alt="Kramer" width="110" height="auto"
                 style="display:block;max-width:110px;filter:{logo_filter};opacity:0.9;">
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td class="og-body" bgcolor="#ffffff"
        style="background-color:#ffffff;padding:32px 36px;">
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
    <td class="og-footer" bgcolor="#1a1a2e"
        style="background-color:#1a1a2e;padding:22px 36px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="color:#D8D8D8;font-size:12px;line-height:1.7;
                     font-family:'Segoe UI',Calibri,Arial,sans-serif;">
            <strong style="color:#D8D8D8;">IT Support Team</strong><br>
            <a href="mailto:servicedesk@kramerav.com"
               style="color:#69FFC3;text-decoration:none;">servicedesk@kramerav.com</a>
          </td>
          <td style="text-align:right;vertical-align:middle;">
            <img src="{logo_footer_url}" alt="Kramer" width="110" height="auto"
                 style="display:block;max-width:110px;opacity:0.95;margin-left:auto;">
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


def _row(label: str, value: str, color: str = '#8205B4') -> str:
    return (f'<tr>'
            f'<td style="color:{color};font-weight:600;white-space:nowrap;width:140px;'
            f'    vertical-align:top;padding:4px 16px 4px 0;">{_esc(label)}</td>'
            f'<td style="color:#333333;vertical-align:top;padding:4px 0;">{_esc(value)}</td>'
            f'</tr>')


def _send_sla_breach_email(ticket):
    name = _esc(ticket.assignee.display_name or ticket.assignee.email)
    deadline = ticket.sla_deadline.strftime('%d %b %Y %H:%M') if ticket.sla_deadline else 'N/A'
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='SLA Deadline Breached',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {ticket.title}',
        header_color='#BE0078',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'The following ticket has <strong>breached its SLA deadline</strong> '
                  f'and requires your immediate attention.'),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}', '#BE0078') +
            _row('Requester', f'{ticket.requester_name} ({ticket.requester_email})', '#BE0078') +
            _row('SLA Deadline', deadline, '#BE0078')
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
    name = _esc(ticket.assignee.display_name or ticket.assignee.email)
    deadline = ticket.sla_deadline.strftime('%d %b %Y %H:%M') if ticket.sla_deadline else 'N/A'
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='SLA Warning',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {ticket.title}',
        header_color='#BE0078',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'This ticket is at <strong>{ticket.sla_percent_elapsed}% of its SLA window</strong>. '
                  f'Please respond soon to avoid a breach.'),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}', '#BE0078') +
            _row('SLA Deadline', deadline, '#BE0078') +
            _row('Elapsed', f'{ticket.sla_percent_elapsed}%', '#BE0078')
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
    name = _esc(ticket.requester_name or ticket.requester_email)
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
    name = _esc(ticket.requester_name or ticket.requester_email)
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


@shared_task(name='tasks.send_requester_comment')
def send_requester_comment(ticket_pk: int, comment_pk: int):
    """Email the requester when an admin posts a public comment on their ticket."""
    from tickets.models import Ticket, TicketComment
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
        comment = TicketComment.objects.select_related('author').get(pk=comment_pk)
    except (Ticket.DoesNotExist, TicketComment.DoesNotExist):
        return
    if not ticket.requester_email:
        return
    name = _esc(ticket.requester_name or ticket.requester_email)
    author_name = _esc(comment.author.display_name or comment.author.email if comment.author else 'IT Support')
    body = _email_html(
        header_title='New reply on your ticket',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {ticket.title}',
        greeting=(
            f'Hi <strong>{name}</strong>,<br><br>'
            f'<strong>{author_name}</strong> from the IT team has posted a reply on your support ticket.'
        ),
        body_rows=(
            _row('Ticket #', f'#{ticket.pk:04d}') +
            _row('Subject', ticket.title) +
            _row('Reply', comment.body[:300] + ('…' if len(comment.body) > 300 else ''))
        ),
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Ticket #{ticket.pk:04d}] New reply from IT Support',
        body=body,
    )
    logger.info(f'[Requester] Comment notification sent for ticket #{ticket_pk}, comment #{comment_pk}.')


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
        name = _esc(ticket.assignee.display_name or ticket.assignee.email)
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
        name = _esc(ticket.assignee.display_name or ticket.assignee.email)
        actor_name_esc = _esc(actor_name)
        body = _email_html(
            header_title='Ticket Updated',
            header_subtitle=f'#{ticket.pk:04d} — {ticket.title}',
            greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                      f'Ticket <strong>#{ticket.pk:04d}</strong> was updated by <strong>{actor_name_esc}</strong>.'),
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


def _send_maintenance_complete_announcement(change):
    """Send a Maintenance Completed broadcast email to all affected employees."""
    from changes.models import Change
    from tickets.models import SystemSetting as _SS
    if _SS.get('emails_enabled', '1') != '1':
        logger.info(f'[Change] Completion announcement skipped — emails disabled.')
        return

    from tickets.models import SystemSetting
    region_recipients = {
        Change.REGION_ISRAEL: SystemSetting.get('change_broadcast_il', 'IL_All_Employees@kramerav.com'),
        Change.REGION_GLOBAL: SystemSetting.get('change_broadcast_global', 'GLOBAL_All_Employees@kramerav.com'),
    }
    to_email = region_recipients.get(change.affected_region)
    if not to_email:
        logger.warning(f'[Change] Unknown region "{change.affected_region}" — skipping completion broadcast.')
        return

    system_str = change.affected_system_display
    date_str = change.planned_date.strftime('%A, %d %B %Y') if change.planned_date else 'N/A'
    region_str = change.get_affected_region_display()

    body = _email_html(
        header_title='Maintenance Completed',
        header_subtitle=f'{system_str} — {date_str}',
        header_color='#69FFC3',
        header_text_color='#1a1a2e',
        greeting=(
            'Dear Employees,<br><br>'
            f'The IT Department is pleased to inform you that the maintenance work on '
            f'<strong>{system_str}</strong> has been <strong>completed successfully</strong>.<br><br>'
            'All systems should be fully operational. If you experience any issues, please contact '
            '<a href="mailto:servicedesk@kramerav.com" style="color:#8205B4;">servicedesk@kramerav.com</a>.'
        ),
        body_rows=(
            _row('System', system_str) +
            _row('Date', date_str) +
            _row('Region', region_str)
        ),
    )
    subject = f'[Maintenance Completed] {system_str} — {date_str}'
    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=settings.SERVICEDESK_EMAIL,
            bcc_email=to_email,
            subject=subject,
            body_html=body,
        )
        logger.info(f'[Change] Completion announcement sent (BCC) to {to_email} for change #{change.pk}.')
    except Exception as exc:
        logger.error(f'[Change] Failed to send completion announcement: {exc}')


def _send_maintenance_announcement(change):
    """Send a Planned Maintenance broadcast email to all affected employees."""
    import os
    from changes.models import Change
    from tickets.models import SystemSetting as _SS
    if _SS.get('emails_enabled', '1') != '1':
        logger.info(f'[Change] Maintenance announcement skipped — emails disabled.')
        return

    # Determine recipient list (configurable via Settings page)
    from tickets.models import SystemSetting
    region_recipients = {
        Change.REGION_ISRAEL: SystemSetting.get('change_broadcast_il', 'IL_All_Employees@kramerav.com'),
        Change.REGION_GLOBAL: SystemSetting.get('change_broadcast_global', 'GLOBAL_All_Employees@kramerav.com'),
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
            '<a href="mailto:servicedesk@kramerav.com" style="color:#8205B4;">servicedesk@kramerav.com</a>.'
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
    from tickets.models import SystemSetting
    if SystemSetting.get('emails_enabled', '1') != '1':
        logger.info(f'[Notification] Skipped email to {to} — emails disabled.')
        return
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


# ── User self-close notification ──────────────────────────────────────────────

@shared_task(name='tasks.notify_user_closed_ticket')
def notify_user_closed_ticket(ticket_pk: int, actor_pk: int):
    """Notify the assignee (if any) that the requester self-closed their ticket."""
    from tickets.models import Ticket
    from users.models import User
    try:
        ticket = Ticket.objects.select_related('assignee').get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return

    if not ticket.assignee:
        return

    try:
        actor = User.objects.get(pk=actor_pk)
    except User.DoesNotExist:
        actor = None

    actor_name = _esc(actor.display_name or actor.email if actor else ticket.requester_name or 'The requester')
    assignee_name = _esc(ticket.assignee.display_name or ticket.assignee.email)
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'

    body = _email_html(
        header_title='Ticket Closed by Requester',
        header_subtitle=f'#{ticket.pk:04d} — {ticket.title}',
        greeting=(f'Hi <strong>{assignee_name}</strong>,<br><br>'
                  f'<strong>{actor_name}</strong> has self-closed their ticket, indicating '
                  f'they resolved the issue on their own. No further action is needed.'),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}') +
            _row('Requester', f'{ticket.requester_name} ({ticket.requester_email})') +
            _row('Closed by', actor_name)
        ),
        cta_url=ticket_url,
        cta_label='View Ticket',
    )
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Kdesk] Closed by Requester — #{ticket.pk:04d}: {ticket.title}',
        body=body,
    )
    logger.info(f'[Portal] Self-close notification sent for ticket #{ticket_pk}.')


# ── @mention in internal notes ───────────────────────────────────────────────

@shared_task(name='tasks.notify_mention')
def notify_mention(ticket_pk: int, note_pk: int, mentioned_user_pk: int, author_pk: int):
    """Notify an admin that they were @mentioned in an internal note."""
    from tickets.models import Ticket, TicketComment
    from users.models import User
    try:
        ticket  = Ticket.objects.select_related('assignee').get(pk=ticket_pk)
        note    = TicketComment.objects.get(pk=note_pk)
        mentioned = User.objects.get(pk=mentioned_user_pk)
        author  = User.objects.get(pk=author_pk)
    except Exception:
        return

    ticket_url   = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    author_name  = _esc(author.display_name or author.email)
    mention_name = _esc(mentioned.display_name or mentioned.email)

    body = _email_html(
        header_title='You were mentioned in an internal note',
        header_subtitle=f'#{ticket.pk:04d} — {ticket.title}',
        greeting=(
            f'Hi <strong>{mention_name}</strong>,<br><br>'
            f'<strong>{author_name}</strong> mentioned you in an internal note '
            f'on ticket <strong>#{ticket.pk:04d}</strong>.'
        ),
        body_rows=(
            _row('Ticket', f'#{ticket.pk:04d} — {ticket.title}') +
            _row('Requester', f'{ticket.requester_name or ""} ({ticket.requester_email})') +
            _row('Note', note.body[:300] + ('…' if len(note.body) > 300 else ''))
        ),
        cta_url=ticket_url,
        cta_label='View Ticket',
    )
    _send_notification_email(
        to=mentioned.email,
        subject=f'[Kdesk] You were mentioned — #{ticket.pk:04d}: {ticket.title}',
        body=body,
    )
    logger.info(f'[Mention] Notified {mentioned.email} about mention in ticket #{ticket_pk}.')


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
            model='llama-3.3-70b-versatile',
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
    submitter_name = _esc(
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
                    header_color='#69FFC3',
                    header_text_color='#1a1a2e',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Your change request has been <strong>approved</strong>. '
                              f'You may now proceed with implementation.'),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}') +
                        _row('Planned Date', planned)
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
                    header_color='#BE0078',
                    greeting=(f'Hi <strong>{submitter_name}</strong>,<br><br>'
                              f'Your change request has been reviewed and was <strong>not approved</strong> '
                              f'at this time. Please reach out to the IT Manager for more information.'),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}', '#BE0078') +
                        _row('Planned Date', planned, '#BE0078')
                    ),
                    cta_url=change_url,
                    cta_label='View in Kdesk',
                ),
            )

    elif event == 'changes_requested':
        if submitter_email:
            remarks = change.manager_remarks or ''
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Changes Requested — #{change.pk:04d}: {change.title}',
                body=_email_html(
                    header_title='Changes Requested',
                    header_subtitle=f'#{change.pk:04d} — {change.title}',
                    header_color='#BE0078',
                    greeting=(
                        f'Hi <strong>{submitter_name}</strong>,<br><br>'
                        f'The IT Manager has reviewed your change request and is requesting some modifications '
                        f'before it can be approved. Please review the remarks below, update your change request, '
                        f'and resubmit it for approval.'
                    ),
                    body_rows=(
                        _row('Change', f'#{change.pk:04d} — {change.title}', '#BE0078') +
                        _row('Planned Date', planned, '#BE0078') +
                        _row('IT Manager Remarks', remarks, '#BE0078')
                    ),
                    cta_url=change_url,
                    cta_label='Review &amp; Update in Kdesk',
                ),
            )

    elif event == 'resubmitted':
        subject = f'[Kdesk] Change Resubmitted for Approval — #{change.pk:04d}: {change.title}'
        body = _email_html(
            header_title='Change Resubmitted for Approval',
            header_subtitle=f'#{change.pk:04d} — {change.title}',
            greeting=(
                f'A change request that you previously requested modifications for has been updated '
                f'and resubmitted for your approval.'
            ),
            body_rows=change_rows,
            cta_url=change_url,
            cta_label='Review &amp; Approve in Kdesk',
        )
        for mgr_email in it_manager_emails:
            _send_notification_email(to=mgr_email, subject=subject, body=body)

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
        # Broadcast completion to affected employees
        _send_maintenance_complete_announcement(change)

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
    submitter_name = _esc(change.submitted_by.display_name or change.submitted_by.email)
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

    system_esc = _esc(change.affected_system_display)

    if reminder_type == 'start':
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as In Progress'
        header_title = 'Action Needed — Mark as In Progress'
        action_label = 'Mark as In Progress'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{system_esc}</strong> '
            f'has started ({timeframe}). Please mark the change as <strong>In Progress</strong> '
            f'in Kdesk so the team knows the work has begun.'
        )
    elif reminder_type == 'done_followup':
        subject = f'[Kdesk] Action Required: Change #{change.pk:04d} Still Not Closed'
        header_title = 'Action Required — Change Not Yet Closed'
        action_label = 'Mark as Done'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{system_esc}</strong> '
            f'ended over an hour ago ({timeframe}), but the change has not been marked as '
            f'<strong>Done</strong> yet. Please update the status as soon as the work is complete.'
        )
    else:
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as Done'
        header_title = 'Action Needed — Mark as Done'
        action_label = 'Mark as Done'
        greeting = (
            f'Hi <strong>{submitter_name}</strong>,<br><br>'
            f'The planned maintenance window for <strong>{system_esc}</strong> '
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


# ── Weekly digest ────────────────────────────────────────────────────────────

@shared_task(name='tasks.send_weekly_digest')
def send_weekly_digest():
    """
    Send each admin a personal ticket digest.
    Superusers also receive the full org-wide summary.
    Runs Saturday night (scheduled via CrontabSchedule).
    """
    from tickets.models import Ticket
    from users.models import User
    from datetime import timedelta

    now   = timezone.now()
    week_ago = now - timedelta(days=7)

    admins = User.objects.filter(is_admin=True, is_active=True)

    for admin in admins:
        my_qs = Ticket.objects.filter(assignee=admin)

        open_count    = my_qs.exclude(status__in=Ticket.TERMINAL_STATUSES).count()
        closed_week   = my_qs.filter(status__in=Ticket.TERMINAL_STATUSES, updated_at__gte=week_ago).count()
        breached      = my_qs.filter(sla_breached=True).exclude(status__in=Ticket.TERMINAL_STATUSES).count()
        open_tickets  = (
            my_qs.exclude(status__in=Ticket.TERMINAL_STATUSES)
            .order_by('sla_deadline')[:10]
        )

        ticket_rows = ''
        for t in open_tickets:
            url = f'{settings.SITE_URL}/tickets/{t.pk}/'
            sla_str = t.sla_deadline.strftime('%d %b %H:%M') if t.sla_deadline else '—'
            status_label = dict(Ticket.STATUS_CHOICES).get(t.status, t.status)
            ticket_rows += (
                f'<tr>'
                f'<td style="padding:4px 8px;"><a href="{url}" style="color:#8205B4;font-weight:600;">#{t.pk:04d}</a></td>'
                f'<td style="padding:4px 8px;">{_esc(t.title[:60])}</td>'
                f'<td style="padding:4px 8px;">{_esc(status_label)}</td>'
                f'<td style="padding:4px 8px;">{_esc(sla_str)}</td>'
                f'</tr>'
            )

        tickets_table = ''
        if ticket_rows:
            tickets_table = (
                '<table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;color:#333;">'
                '<tr style="background:#f0f0f0;">'
                '<th style="padding:6px 8px;text-align:left;">#</th>'
                '<th style="padding:6px 8px;text-align:left;">Subject</th>'
                '<th style="padding:6px 8px;text-align:left;">Status</th>'
                '<th style="padding:6px 8px;text-align:left;">SLA</th>'
                '</tr>'
                + ticket_rows +
                '</table>'
            )
        else:
            tickets_table = '<p style="color:#888;font-size:13px;">No open tickets — great work! 🎉</p>'

        extra_section = ''
        if admin.is_superuser:
            total_open    = Ticket.objects.exclude(status__in=Ticket.TERMINAL_STATUSES).count()
            total_closed  = Ticket.objects.filter(status__in=Ticket.TERMINAL_STATUSES, updated_at__gte=week_ago).count()
            total_breached = Ticket.objects.filter(sla_breached=True).exclude(status__in=Ticket.TERMINAL_STATUSES).count()
            extra_section = (
                '<br>'
                + _row('Org — Total Open', str(total_open))
                + _row('Org — Closed This Week', str(total_closed))
                + _row('Org — SLA Breached', str(total_breached))
            )

        admin_name = _esc(admin.display_name or admin.email)
        kdesk_url  = f'{settings.SITE_URL}/tickets/'

        body = _email_html(
            header_title='Your Weekly Ticket Digest',
            header_subtitle=f'Week ending {now.strftime("%d %b %Y")}',
            greeting=(
                f'Hi <strong>{admin_name}</strong>,<br><br>'
                f'Here is your weekly summary of tickets assigned to you.'
            ),
            body_rows=(
                _row('Open Tickets', str(open_count)) +
                _row('Closed This Week', str(closed_week)) +
                _row('SLA Breached (open)', str(breached)) +
                extra_section
            ),
            cta_url=kdesk_url,
            cta_label='View All Tickets',
        )

        # Append open tickets table after the standard template
        body = body.replace(
            '</body>',
            f'<div style="max-width:600px;margin:0 auto 24px;padding:0 24px;">'
            f'<p style="font-size:14px;color:#333;font-weight:600;margin-bottom:8px;">Your Open Tickets</p>'
            f'{tickets_table}</div></body>'
        )

        _send_notification_email(
            to=admin.email,
            subject=f'[Kdesk] Weekly Digest — {now.strftime("%d %b %Y")}',
            body=body,
        )
        logger.info(f'[Digest] Sent weekly digest to {admin.email}.')


# ── Setup scheduled tasks in the DB ──────────────────────────────────────────

def register_periodic_tasks():
    """
    Called from a management command on first run to seed the Celery Beat schedule.
    """
    from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule

    poll_interval, _ = IntervalSchedule.objects.get_or_create(
        every=30,
        period=IntervalSchedule.SECONDS,
    )
    sync_interval, _ = IntervalSchedule.objects.get_or_create(every=60, period=IntervalSchedule.MINUTES)
    sla_interval, _ = IntervalSchedule.objects.get_or_create(every=15, period=IntervalSchedule.MINUTES)

    # Saturday at 20:00 Israel time (UTC+3) = 17:00 UTC; day_of_week=6 = Saturday
    digest_cron, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='17', day_of_week='6',
        day_of_month='*', month_of_year='*',
        defaults={'timezone': 'UTC'},
    )

    interval_tasks = [
        ('Poll Mailbox',         'tasks.poll_mailbox',         poll_interval),
        ('Sync Entra Users',     'tasks.sync_users',           sync_interval),
        ('Sync Entra Admins',    'tasks.sync_admins',          sync_interval),
        ('Check SLA',            'tasks.check_sla',            sla_interval),
        ('Check Change Reminders', 'tasks.check_change_reminders', sla_interval),
    ]
    for name, task_name, schedule in interval_tasks:
        PeriodicTask.objects.get_or_create(
            name=name,
            defaults={'task': task_name, 'interval': schedule, 'enabled': True},
        )

    PeriodicTask.objects.get_or_create(
        name='Weekly Digest',
        defaults={'task': 'tasks.send_weekly_digest', 'crontab': digest_cron, 'enabled': True},
    )
