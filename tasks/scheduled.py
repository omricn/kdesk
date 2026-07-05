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
    import os
    # Safety guard: only poll in production. Without this flag, a local dev
    # Celery worker would process the live mailbox and steal emails before
    # production sees them, causing lost tickets and wrong confirmation emails.
    if os.environ.get('ENABLE_EMAIL_POLLING', '').lower() != 'true':
        logger.info('[Task] poll_mailbox skipped — ENABLE_EMAIL_POLLING not set.')
        return
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
                header_color: str = '#8205B4', header_text_color: str = '#ffffff',
                cta_raw: str = None, extra_html: str = '') -> str:
    """
    Render a fully branded Kramer email.
    body_rows: HTML rows for the details table (tr elements).
    header_text_color: '#ffffff' for dark headers, '#1a1a2e' for light headers (e.g. green).
    cta_raw: optional raw HTML block used as the CTA (overrides cta_url/cta_label).
    extra_html: optional raw HTML appended inside the body after the CTA block.
    """
    logo_url = f'{settings.SITE_URL}/static/img/kramer_logo.png'
    logo_footer_url = f'{settings.SITE_URL}/static/img/kramer_logo_footer.png'
    subtitle_opacity = '0.65' if header_text_color != '#ffffff' else '0.82'
    logo_filter = 'brightness(0)' if header_text_color != '#ffffff' else 'brightness(0) invert(1)'

    cta_block = ''
    if cta_raw:
        cta_block = f'<tr><td style="padding:24px 0 8px;">{cta_raw}</td></tr>'
    elif cta_url and cta_label:
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
          <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f5f5f5"
                 style="background:#f5f5f5;border-left:4px solid {header_color};border-radius:4px;">
            <tr><td bgcolor="#f5f5f5" style="padding:18px 22px;background:#f5f5f5;">
              <table width="100%" cellpadding="5" cellspacing="0" bgcolor="#f5f5f5"
                     style="background:#f5f5f5;font-size:14px;color:#333333;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
                {body_rows}
              </table>
            </td></tr>
          </table>
        </td></tr>'''

    font_stack = f"'GT Eesti Display Lt','GT Eesti Display','Segoe UI',Calibri,Arial,sans-serif"
    font_stack_md = f"'GT Eesti Display Md','GT Eesti Display','Segoe UI',Calibri,Arial,sans-serif"
    font_url_lt = f'{settings.SITE_URL}/static/fonts/GT-Eesti-Display-Light.woff2'
    font_url_md = f'{settings.SITE_URL}/static/fonts/GT-Eesti-Display-Medium.woff2'

    return f'''<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <style>
    @font-face {{font-family:'GT Eesti Display Lt';src:url('{font_url_lt}') format('woff2');font-weight:normal;font-style:normal;}}
    @font-face {{font-family:'GT Eesti Display Md';src:url('{font_url_md}') format('woff2');font-weight:normal;font-style:normal;}}
    :root{{color-scheme:light only;}}
    [data-ogsc] .og-header{{background-color:{header_color}!important;}}
    [data-ogsc] .og-body{{background-color:#ffffff!important;color:#333333!important;}}
    [data-ogsc] .og-footer{{background-color:#1a1a2e!important;}}
    [data-ogsb] .og-header{{background-color:{header_color}!important;}}
    [data-ogsb] .og-body{{background-color:#ffffff!important;}}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f0f0f0;color-scheme:light;
             font-family:{font_stack};" bgcolor="#f0f0f0">
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
                      text-transform:uppercase;opacity:{subtitle_opacity};font-family:{font_stack};">
              IT Support
            </p>
            <h1 style="margin:4px 0 0;color:{header_text_color};font-size:22px;font-weight:700;
                       font-family:{font_stack_md};line-height:1.3;">
              {_esc(header_title)}
            </h1>
            {f'<p dir="auto" style="margin:4px 0 0;color:{header_text_color};opacity:{subtitle_opacity};font-size:13px;font-family:{font_stack};">{_esc(header_subtitle)}</p>' if header_subtitle else ''}
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
                       font-family:{font_stack};line-height:1.6;">
          {greeting}
        </td></tr>
        {details_block}
        {cta_block}
        {f'<tr><td style="padding-top:8px;">{extra_html}</td></tr>' if extra_html else ''}
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
                     font-family:{font_stack};">
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


def _row(label: str, value: str, color: str = '#000000') -> str:
    return (f'<tr>'
            f'<td style="color:{color};font-weight:700;white-space:nowrap;width:160px;'
            f"    vertical-align:top;padding:4px 16px 4px 0;font-family:'GT Eesti Display Md','GT Eesti Display','Segoe UI',Calibri,Arial,sans-serif;"
            f'>{_esc(label)}</td>'
            f'<td dir="auto" style="color:#000000;vertical-align:top;padding:4px 0;unicode-bidi:plaintext;">{_esc(value)}</td>'
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
        subject=f'[Ticket #{ticket.pk:04d}] SLA Breached — {ticket.title}',
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
        subject=f'[Ticket #{ticket.pk:04d}] SLA Warning — {ticket.title}',
        body=body,
    )


# ── Requester emails ──────────────────────────────────────────────────────────

@shared_task(name='tasks.send_requester_created')
def send_requester_created(ticket_pk: int):
    """Email the requester confirming their ticket was received."""
    from tickets.models import Ticket
    from datetime import timedelta
    logger.info('[Requester] send_requester_created called for ticket_pk=%s', ticket_pk)
    try:
        ticket = Ticket.objects.get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        logger.warning('[Requester] send_requester_created: ticket #%s not found — skipping.', ticket_pk)
        return
    # Guard against stale queued tasks firing for old tickets.
    # A confirmation email should only go out within 2 hours of ticket creation.
    if ticket.created_at and timezone.now() - ticket.created_at > timedelta(hours=2):
        logger.warning(
            '[Requester] send_requester_created: ticket #%s is too old (%s) — skipping stale task.',
            ticket_pk, ticket.created_at,
        )
        return
    name = _esc(ticket.requester_name or ticket.requester_email)
    submitted = timezone.localtime(ticket.created_at).strftime('%d %b %Y %H:%M') if ticket.created_at else 'N/A'
    portal_url = f'{settings.SITE_URL}/portal/tickets/{ticket.pk}/'
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
        cta_url=portal_url,
        cta_label='View Your Ticket',
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Ticket #{ticket.pk:04d}] Your request has been received',
        body=body,
    )
    logger.info(f'[Requester] Creation confirmation sent for ticket #{ticket_pk}.')


@shared_task(name='tasks.send_assignee_user_replied')
def send_assignee_user_replied(ticket_pk: int):
    """Notify the assigned admin when a user replies to their ticket."""
    from tickets.models import Ticket
    try:
        ticket = Ticket.objects.select_related('assignee').get(pk=ticket_pk)
    except Ticket.DoesNotExist:
        return
    if not ticket.assignee or not ticket.assignee.email:
        return
    requester = _esc(ticket.requester_name or ticket.requester_email)
    ticket_url = f'{settings.SITE_URL}/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='User Replied',
        header_subtitle=f'Ticket #{ticket.pk:04d} — {_esc(ticket.title)}',
        greeting=(
            f'Hi <strong>{_esc(ticket.assignee.display_name or ticket.assignee.email)}</strong>,<br><br>'
            f'<strong>{requester}</strong> has replied to a ticket assigned to you.'
        ),
        body_rows=(
            _row('Ticket #', f'#{ticket.pk:04d}') +
            _row('Subject', _esc(ticket.title)) +
            _row('From', requester)
        ),
        cta_url=ticket_url,
        cta_label='View Ticket',
    )
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Ticket #{ticket.pk:04d}] User replied — {ticket.title}',
        body=body,
    )
    logger.info(f'[Assignee] User-replied notification sent for ticket #{ticket_pk}.')


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
    import base64, mimetypes
    name = _esc(ticket.requester_name or ticket.requester_email)
    closed = timezone.localtime(ticket.resolved_at).strftime('%d %b %Y %H:%M') if ticket.resolved_at else 'N/A'
    solution_row = _row('Resolution', ticket.solution) if ticket.solution else ''
    # Embed solution images inline as base64 so they render without auth
    sol_imgs = ticket.attachments.filter(is_solution_image=True)
    if sol_imgs.exists():
        imgs_html = ''
        for att in sol_imgs:
            try:
                mime = mimetypes.guess_type(att.filename)[0] or 'image/png'
                with att.file.open('rb') as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                imgs_html += (
                    f'<img src="data:{mime};base64,{b64}" alt="Solution screenshot" '
                    f'style="max-width:100%;height:auto;border-radius:6px;margin-top:8px;display:block;">'
                )
            except Exception:
                pass
        if imgs_html:
            solution_row += (
                '<tr><td colspan="2" style="padding:4px 16px 12px;">'
                + imgs_html + '</td></tr>'
            )
    ticket_url = f'{settings.SITE_URL}/portal/tickets/{ticket.pk}/'
    body = _email_html(
        header_title='Your ticket has been closed',
        header_subtitle=f'Ticket #{ticket.pk:04d}',
        greeting=(f'Hi <strong>{name}</strong>,<br><br>'
                  f'Your support ticket has been resolved and closed. '
                  f'We\'d love to hear how we did — please take a moment to rate your experience.'),
        body_rows=(
            _row('Ticket #', f'#{ticket.pk:04d}') +
            _row('Subject', ticket.title) +
            _row('Closed', closed) +
            solution_row
        ),
        cta_url=ticket_url,
        cta_label='⭐ Rate Your Experience',
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Ticket #{ticket.pk:04d}] Ticket Closed — {ticket.title}',
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
    portal_url = f'{settings.SITE_URL}/portal/tickets/{ticket.pk}/'
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
        cta_url=portal_url,
        cta_label=f'Reply to {author_name} on the Kdesk portal',
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
            subject=f'[Ticket #{ticket.pk:04d}] Ticket Assigned — {ticket.title}',
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
            subject=f'[Ticket #{ticket.pk:04d}] Ticket Updated — {ticket.title}',
            body=body,
        )


def _change_window_end(change):
    """Return the tz-aware datetime when the maintenance window ends, or None if unknown."""
    from datetime import datetime, timedelta
    if not change.planned_date or not change.planned_to:
        return None
    return timezone.make_aware(datetime.combine(change.planned_date, change.planned_to))


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
        header_title=f'Maintenance Completed — {_esc(system_str)}',
        header_subtitle=date_str,
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
            to_email=change.submitted_by.email,
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
        timeframe_str = f'{change.planned_from.strftime("%H:%M")} – {change.planned_to.strftime("%H:%M")} [Israel Time]'
    elif change.planned_from:
        timeframe_str = f'From {change.planned_from.strftime("%H:%M")} [Israel Time]'
    else:
        timeframe_str = 'To be confirmed'

    system_str = change.affected_system_display
    region_str = change.get_affected_region_display()

    body = _email_html(
        header_title=f'Planned Maintenance — {_esc(system_str)}',
        header_subtitle=f'{date_str} · {timeframe_str}',
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
            to_email=change.submitted_by.email,
            bcc_email=to_email,
            subject=subject,
            body_html=body,
        )
        logger.info(f'[Change] Maintenance announcement sent (BCC) to {to_email} for change #{change.pk}.')
    except Exception as exc:
        logger.error(f'[Change] Failed to send maintenance announcement: {exc}')


def _send_upcoming_maintenance_broadcast(change):
    """Send a 3-hour-before reminder to the submitter, BCC to the relevant employee group."""
    from changes.models import Change
    from tickets.models import SystemSetting
    if SystemSetting.get('emails_enabled', '1') != '1':
        logger.info(f'[Change] Upcoming broadcast skipped — emails disabled.')
        return

    if not change.submitted_by:
        return

    region_recipients = {
        Change.REGION_ISRAEL: SystemSetting.get('change_broadcast_il', 'IL_All_Employees@kramerav.com'),
        Change.REGION_GLOBAL: SystemSetting.get('change_broadcast_global', 'GLOBAL_All_Employees@kramerav.com'),
    }
    bcc_email = region_recipients.get(change.affected_region)
    if not bcc_email:
        logger.warning(f'[Change] Unknown region "{change.affected_region}" — skipping upcoming broadcast.')
        return

    date_str = change.planned_date.strftime('%A, %d %B %Y') if change.planned_date else 'TBD'
    if change.planned_from and change.planned_to:
        timeframe_str = f'{change.planned_from.strftime("%H:%M")} – {change.planned_to.strftime("%H:%M")} [Israel Time]'
    elif change.planned_from:
        timeframe_str = f'From {change.planned_from.strftime("%H:%M")} [Israel Time]'
    else:
        timeframe_str = 'To be confirmed'

    system_str = change.affected_system_display
    region_str = change.get_affected_region_display()

    body = _email_html(
        header_title=f'Maintenance in ~3 Hours — {_esc(system_str)}',
        header_subtitle=f'{date_str} · {timeframe_str}',
        greeting=(
            'Dear Employees,<br><br>'
            'This is a reminder that a <strong>Planned Maintenance</strong> window is starting '
            '<strong>in approximately 3 hours</strong>. The affected system may be temporarily '
            'unavailable during this time.<br><br>'
            'Please save your work and plan accordingly. '
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

    subject = f'[Reminder] Planned Maintenance in ~3 Hours — {system_str}, {timeframe_str}'
    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=change.submitted_by.email,
            bcc_email=bcc_email,
            subject=subject,
            body_html=body,
        )
        logger.info(f'[Change] Upcoming broadcast sent (BCC) to {bcc_email} for change #{change.pk}.')
    except Exception as exc:
        logger.error(f'[Change] Failed to send upcoming broadcast: {exc}')


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
        subject=f'[Ticket #{ticket.pk:04d}] Closed by Requester — {ticket.title}',
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
        subject=f'[Ticket #{ticket.pk:04d}] You were mentioned — {ticket.title}',
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
        # Broadcast maintenance announcement only if enabled and the window hasn't ended yet
        if change.notify_employees:
            window_end = _change_window_end(change)
            if window_end is None or timezone.now() < window_end:
                _send_maintenance_announcement(change)
            else:
                logger.info(f'[Change] Skipping approval broadcast for #{change.pk} — approved after maintenance window.')
        else:
            logger.info(f'[Change] Skipping approval broadcast for #{change.pk} — employee notification disabled.')
        # Add calendar event to every GLOBAL_OPT_IT member's calendar
        create_change_calendar_events.delay(change_pk)

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
        # Broadcast completion only if enabled and done within 3 hours of the window ending
        if change.notify_employees:
            from datetime import timedelta
            window_end = _change_window_end(change)
            if window_end is None or timezone.now() < window_end + timedelta(hours=3):
                _send_maintenance_complete_announcement(change)
            else:
                logger.info(f'[Change] Skipping completion broadcast for #{change.pk} — marked done 3h+ after maintenance window.')
        else:
            logger.info(f'[Change] Skipping completion broadcast for #{change.pk} — employee notification disabled.')

    logger.info(f'[Change] Notification sent for change #{change_pk}, event={event}')


@shared_task(name='tasks.create_change_calendar_events')
def create_change_calendar_events(change_pk: int):
    """Create a calendar event on every GLOBAL_OPT_IT member's calendar when a change is approved."""
    from datetime import datetime, date
    from changes.models import Change
    from tickets.models import SystemSetting
    try:
        change = Change.objects.get(pk=change_pk)
    except Change.DoesNotExist:
        return

    if not change.planned_date:
        logger.warning(f'[Calendar] Change #{change_pk} has no planned_date — skipping calendar events.')
        return

    # Build ISO start/end strings (naive — Graph interprets them in the given timeZone)
    planned_from = change.planned_from
    planned_to   = change.planned_to
    if planned_from:
        start_dt = datetime.combine(change.planned_date, planned_from)
    else:
        start_dt = datetime.combine(change.planned_date, datetime.min.time().replace(hour=8))
    if planned_to:
        end_dt = datetime.combine(change.planned_date, planned_to)
    else:
        from datetime import timedelta
        end_dt = start_dt + timedelta(hours=2)

    start_iso = start_dt.strftime('%Y-%m-%dT%H:%M:%S')
    end_iso   = end_dt.strftime('%Y-%m-%dT%H:%M:%S')

    system_str  = change.affected_system_display
    region_str  = change.get_affected_region_display()
    date_str    = change.planned_date.strftime('%A, %d %B %Y')
    timeframe   = (
        f'{start_dt.strftime("%H:%M")} – {end_dt.strftime("%H:%M")}'
        if planned_from else 'TBD'
    )
    submitter   = change.submitted_by.display_name if change.submitted_by else '—'
    created_str = change.created_at.strftime('%d %B %Y %H:%M') if change.created_at else '—'
    risk_str    = change.get_risk_level_display()
    change_url  = f'{settings.SITE_URL}/changes/{change.pk}/'

    subject = f'[Planned Maintenance] #{change.pk:04d} — {change.title}'

    body_html = _email_html(
        header_title='Planned Maintenance',
        header_subtitle=f'#{change.pk:04d} — {system_str} · {date_str}',
        greeting=(
            'A change request has been <strong>approved</strong> and is scheduled for implementation. '
            'The calendar block has been added to your calendar for reference — '
            'you will <strong>not</strong> be shown as busy during this time.<br><br>'
            'Please review the details below.'
        ),
        body_rows=(
            _row('Change #',         f'#{change.pk:04d}') +
            _row('Title',            change.title) +
            _row('Risk Level',       risk_str) +
            _row('Affected System',  system_str) +
            _row('Affected Region',  region_str) +
            _row('Planned Date',     date_str) +
            _row('Planned Timeframe', timeframe) +
            _row('Submitted By',     submitter) +
            _row('Created',          created_str)
        ),
        cta_url=change_url,
        cta_label='View Change in Kdesk',
    )

    group_name = SystemSetting.get('change_it_calendar_group', '_Global_OPS_IT')
    try:
        from integrations.graph_client import get_client
        client = get_client()
        group_id = client.get_group_id_by_name(group_name)
        members  = client.get_group_members(group_id)
    except Exception as exc:
        logger.error(f'[Calendar] Could not fetch members of {group_name}: {exc}')
        return

    ok = 0
    for member in members:
        email = member.get('mail')
        if not email:
            continue
        try:
            client.create_calendar_event(
                user_email=email,
                subject=subject,
                start_iso=start_iso,
                end_iso=end_iso,
                body_html=body_html,
            )
            ok += 1
        except Exception as exc:
            logger.warning(f'[Calendar] Could not create event for {email}: {exc}')

    logger.info(f'[Calendar] Created calendar events for {ok}/{len(members)} members of {group_name} (change #{change_pk}).')


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
    # Use local (Israel) time for date/time comparisons — planned_from/planned_to
    # are stored as naive local times, so comparing against UTC would be 3 hours off.
    local_now = timezone.localtime(now)
    today = local_now.date()
    current_time = local_now.time()

    # ── Reminder 0: Overdue approval reminder ─────────────────────────────────
    # Changes still in an un-actioned state (New / Pending Approval / Pending Changes)
    # whose planned date has already passed → email submitter + IT Managers.
    UNAPPROVED_STATUSES = [Change.STATUS_NEW, Change.STATUS_PENDING, Change.STATUS_PENDING_CHANGES]
    overdue_candidates = Change.objects.filter(
        status__in=UNAPPROVED_STATUSES,
        planned_date__lt=today,
        reminded_overdue=False,
    ).select_related('submitted_by')

    for change in overdue_candidates:
        _send_change_reminder(change, 'overdue_approval')
        change.reminded_overdue = True
        change.save(update_fields=['reminded_overdue'])
        logger.info(f'[Change] Overdue approval reminder sent for change #{change.pk}.')

    # ── Auto-advance: approved → in_progress when planned window starts ───────
    start_candidates = Change.objects.filter(
        status=Change.STATUS_APPROVED,
        planned_date__lte=today,
        planned_from__isnull=False,
        reminded_start=False,
    ).select_related('submitted_by')

    for change in start_candidates:
        if change.planned_date < today or (change.planned_date == today and change.planned_from <= current_time):
            change.status = Change.STATUS_IN_PROGRESS
            change.reminded_start = True
            change.save(update_fields=['status', 'reminded_start', 'updated_at'])
            logger.info(f'[Change] Auto-advanced change #{change.pk} to in_progress at planned start time.')

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

    # ── Reminder 4: Upcoming broadcast (3 hours before planned_from) ───────────
    # Approved changes whose start is within the next 3 hours — broadcast to
    # the relevant employee group (IL or Global) so everyone knows it's imminent.
    upcoming_candidates = Change.objects.filter(
        status=Change.STATUS_APPROVED,
        planned_date__isnull=False,
        planned_from__isnull=False,
        reminded_upcoming=False,
    )

    for change in upcoming_candidates:
        start_dt = datetime.combine(change.planned_date, change.planned_from)
        start_dt_aware = timezone.make_aware(start_dt)
        if now >= start_dt_aware - timedelta(hours=3) and now < start_dt_aware:
            if change.notify_employees:
                _send_upcoming_maintenance_broadcast(change)
            else:
                logger.info(f'[Change] Skipping upcoming broadcast for #{change.pk} — employee notification disabled.')
            change.reminded_upcoming = True
            change.save(update_fields=['reminded_upcoming'])
            logger.info(f'[Change] Upcoming (3 h) broadcast processed for change #{change.pk}.')


def _send_change_reminder(change, reminder_type: str):
    """Send a status-update reminder email to the change submitter (and IT Managers for overdue)."""
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
        _row('Planned Date', date_str) +
        _row('Status', change.get_status_display()) +
        _row('Timeframe', timeframe)
    )

    system_esc = _esc(change.affected_system_display)

    if reminder_type == 'overdue_approval':
        subject = f'[Kdesk] Overdue: Change #{change.pk:04d} Has Not Been Approved'
        header_title = 'Change Request Overdue — Approval Required'

        submitter_body = _email_html(
            header_title=header_title,
            header_subtitle=f'#{change.pk:04d} — {change.title}',
            header_color='#BE0078',
            greeting=(
                f'Hi <strong>{submitter_name}</strong>,<br><br>'
                f'Your change request for <strong>{system_esc}</strong> was planned for '
                f'<strong>{date_str}</strong> but has not yet been approved.<br><br>'
                f'Current status: <strong>{change.get_status_display()}</strong>. '
                f'Please follow up with the IT Manager.'
            ),
            body_rows=detail_rows,
            cta_url=change_url,
            cta_label='View Change in Kdesk',
        )
        _send_notification_email(to=to_email, subject=subject, body=submitter_body)

        manager_body = _email_html(
            header_title=header_title,
            header_subtitle=f'#{change.pk:04d} — {change.title}',
            header_color='#BE0078',
            greeting=(
                f'A change request submitted by <strong>{submitter_name}</strong> for '
                f'<strong>{system_esc}</strong> was planned for <strong>{date_str}</strong> '
                f'but has not been approved or rejected.<br><br>'
                f'Current status: <strong>{change.get_status_display()}</strong>. '
                f'Please review and take action.'
            ),
            body_rows=detail_rows,
            cta_url=change_url,
            cta_label='Review Change in Kdesk',
        )
        for mgr_email in _get_it_manager_emails():
            _send_notification_email(to=mgr_email, subject=subject, body=manager_body)
        return

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

def _digest_stat_row(stats):
    """Render a row of stat cards. stats = list of (number, label, warn_if_nonzero)."""
    cells = ''
    for i, (num, label, warn) in enumerate(stats):
        num_color = '#BE0078' if warn and num > 0 else '#1a1a1a'
        spacer = '<td style="width:12px;"></td>' if i < len(stats) - 1 else ''
        cells += (
            f'<td style="text-align:center;vertical-align:top;padding:14px 8px;'
            f'border-top:3px solid #8205B4;">'
            f'<div style="font-size:30px;font-weight:700;line-height:1;color:{num_color};'
            f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">{num}</div>"
            f'<div style="font-size:11px;font-weight:600;color:#1a1a1a;margin-top:6px;'
            f"text-transform:uppercase;letter-spacing:0.5px;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">"
            f'{_esc(label)}</div>'
            f'</td>'
            + spacer
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">'
        f'<tr>{cells}</tr>'
        f'</table>'
    )


@shared_task(name='tasks.send_weekly_digest')
def send_weekly_digest(target_email: str = None):
    """
    Send each admin a personal ticket digest.
    Superusers also receive the full org-wide summary.
    Runs Saturday night (scheduled via CrontabSchedule).
    Pass target_email to send only to one recipient (useful for testing).
    """
    from tickets.models import Ticket
    from users.models import User
    from datetime import timedelta

    now      = timezone.now()
    week_ago = now - timedelta(days=7)

    admins = User.objects.filter(is_admin=True, is_active=True)
    if target_email:
        admins = admins.filter(email=target_email)

    for admin in admins:
        my_qs = Ticket.objects.filter(assignee=admin)

        open_count   = my_qs.exclude(status__in=Ticket.TERMINAL_STATUSES).count()
        new_week     = my_qs.filter(created_at__gte=week_ago).count()
        closed_week  = my_qs.filter(status__in=Ticket.TERMINAL_STATUSES, updated_at__gte=week_ago).count()
        breached     = my_qs.filter(sla_breached=True).exclude(status__in=Ticket.TERMINAL_STATUSES).count()

        # ── Stat cards ─────────────────────────────────────────────────────────
        personal_stats_html = _digest_stat_row([
            (open_count,  'Open',           False),
            (new_week,    'New This Week',  False),
            (closed_week, 'Closed',         False),
            (breached,    'SLA Breached',   True),
        ])

        org_stats_html = ''
        if admin.is_superuser:
            total_open     = Ticket.objects.exclude(status__in=Ticket.TERMINAL_STATUSES).count()
            total_new      = Ticket.objects.filter(created_at__gte=week_ago).count()
            total_closed   = Ticket.objects.filter(status__in=Ticket.TERMINAL_STATUSES, updated_at__gte=week_ago).count()
            total_breached = Ticket.objects.filter(sla_breached=True).exclude(status__in=Ticket.TERMINAL_STATUSES).count()
            org_stats_html = (
                f'<p style="font-size:12px;font-weight:700;color:#1a1a1a;margin:20px 0 8px;'
                f"text-transform:uppercase;letter-spacing:0.5px;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">"
                f'Org-Wide</p>'
                + _digest_stat_row([
                    (total_open,     'Total Open',     False),
                    (total_new,      'New This Week',  False),
                    (total_closed,   'Closed',         False),
                    (total_breached, 'SLA Breached',   True),
                ])
            )

        # ── Open tickets table ─────────────────────────────────────────────────
        open_tickets = (
            my_qs.exclude(status__in=Ticket.TERMINAL_STATUSES)
            .order_by('sla_deadline')[:10]
        )
        ticket_rows = ''
        for t in open_tickets:
            url     = f'{settings.SITE_URL}/tickets/{t.pk}/'
            sla_str = t.sla_deadline.strftime('%d %b %H:%M') if t.sla_deadline else '—'
            ticket_rows += (
                f'<tr style="border-bottom:1px solid #dddddd;">'
                f'<td style="padding:6px 8px;white-space:nowrap;vertical-align:top;">'
                f'<a href="{url}" style="color:#8205B4;font-weight:700;text-decoration:none;'
                f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">#{t.pk:04d}</a></td>"
                f'<td style="padding:6px 8px;font-size:13px;color:#1a1a1a;vertical-align:top;'
                f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">{_esc(t.title[:55])}</td>"
                f'<td style="padding:6px 8px;font-size:12px;color:#1a1a1a;white-space:nowrap;vertical-align:top;'
                f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">{_esc(t.get_status_display())}</td>"
                f'<td style="padding:6px 8px;font-size:12px;color:#555555;white-space:nowrap;vertical-align:top;'
                f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">{_esc(sla_str)}</td>"
                f'</tr>'
            )

        if ticket_rows:
            tickets_section = (
                f'<p style="font-size:12px;font-weight:700;color:#1a1a1a;margin:20px 0 8px;'
                f"text-transform:uppercase;letter-spacing:0.5px;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">"
                f'Your Open Tickets</p>'
                f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-top:2px solid #8205B4;">'
                f'<tr>'
                f'<th style="padding:6px 8px;text-align:left;font-size:11px;color:#1a1a1a;font-weight:700;'
                f"text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid #dddddd;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">#</th>"
                f'<th style="padding:6px 8px;text-align:left;font-size:11px;color:#1a1a1a;font-weight:700;'
                f"text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid #dddddd;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">Subject</th>"
                f'<th style="padding:6px 8px;text-align:left;font-size:11px;color:#1a1a1a;font-weight:700;'
                f"text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid #dddddd;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">Status</th>"
                f'<th style="padding:6px 8px;text-align:left;font-size:11px;color:#1a1a1a;font-weight:700;'
                f"text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid #dddddd;font-family:'Segoe UI',Calibri,Arial,sans-serif;\">SLA Due</th>"
                f'</tr>'
                + ticket_rows +
                f'</table>'
            )
        else:
            tickets_section = (
                f'<p style="color:#1a1a1a;font-size:13px;margin:16px 0;'
                f"font-family:'Segoe UI',Calibri,Arial,sans-serif;\">No open tickets — great work! 🎉</p>"
            )

        admin_name = _esc(admin.display_name or admin.email)
        kdesk_url  = f'{settings.SITE_URL}/tickets/'

        body = _email_html(
            header_title='Your Weekly Ticket Digest',
            header_subtitle=f'Week ending {now.strftime("%d %b %Y")}',
            greeting=(
                f'Hi <strong>{admin_name}</strong>,<br><br>'
                f'Here is your weekly ticket summary.'
                f'<br><br>'
                + personal_stats_html
                + org_stats_html
                + tickets_section
            ),
            body_rows='',
            cta_url=kdesk_url,
            cta_label='View All Tickets',
        )

        _send_notification_email(
            to=admin.email,
            subject=f'[Kdesk] Weekly Digest — {now.strftime("%d %b %Y")}',
            body=body,
        )
        logger.info(f'[Digest] Sent weekly digest to {admin.email}.')


# ── Setup scheduled tasks in the DB ──────────────────────────────────────────

@shared_task(name='tasks.sweep_stuck_provisioning')
def sweep_stuck_provisioning():
    """Watchdog: auto-fail provisioning requests stuck in 'claimed' well past the
    agent's max runtime (the KAPPIT task caps a run at 45 min). A request still
    'claimed' after 60 min means the agent crashed or was killed without reporting.
    Flip it to 'failed' so it surfaces as actionable (and Retry-able) instead of
    sitting on 'Running' forever, and alert the superusers."""
    from datetime import timedelta
    from hibob_sync.models import ProvisioningRequest

    threshold = timezone.now() - timedelta(minutes=60)
    stuck = list(ProvisioningRequest.objects.filter(status='claimed', claimed_at__lt=threshold))
    for req in stuck:
        req.status = 'failed'
        req.result_success = False
        req.completed_at = timezone.now()
        req.result_message = (
            'Auto-failed by watchdog: no result reported within 60 minutes — the agent '
            'likely crashed or was killed mid-run. Review the KAPPIT log and use Retry to re-queue.'
        )
        req.save(update_fields=['status', 'result_success', 'completed_at', 'result_message'])
        logger.warning(
            '[Watchdog] Provisioning #%s (%s %s) stuck in claimed since %s — marked failed.',
            req.id, req.first_name, req.last_name, req.claimed_at,
        )
        _alert_stuck_provisioning(req)
    if stuck:
        logger.warning('[Watchdog] Marked %d stuck provisioning request(s) as failed.', len(stuck))
    return len(stuck)


def _alert_stuck_provisioning(req):
    """Email the superusers that a stuck provisioning request was auto-failed."""
    try:
        from integrations.graph_client import get_client
        name = _esc(f'{req.first_name} {req.last_name}'.strip())
        since = req.claimed_at.strftime('%Y-%m-%d %H:%M UTC') if req.claimed_at else 'an unknown time'
        body_html = _email_html(
            header_title='Provisioning stuck — auto-failed',
            header_subtitle=name,
            greeting=(
                f'Provisioning for <strong>{name}</strong> was claimed at {since} but never '
                f'reported a result within 60 minutes, so it has been automatically marked '
                f'<strong>failed</strong>.<br><br>'
                f'The KAPPIT agent likely crashed or was killed mid-run. Check the provisioning '
                f'log on KAPPIT, then use <strong>Retry</strong> on the HiBob Sync dashboard to '
                f're-queue it.'
            ),
            body_rows='',
        )
        get_client().send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email='Kdesk_Superusers@kramerav.com',
            subject=f'⚠️ Provisioning stuck & auto-failed — {req.first_name} {req.last_name}',
            body_html=body_html,
        )
    except Exception as exc:
        logger.warning('[Watchdog] Could not send stuck-provisioning alert for #%s: %s', req.id, exc)


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
        ('Sweep Stuck Provisioning', 'tasks.sweep_stuck_provisioning', sla_interval),
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
