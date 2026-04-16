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


def _send_sla_breach_email(ticket):
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Kdesk] SLA Breached — #{ticket.pk:04d}: {ticket.title}',
        body=f"""
        <p>Hello {ticket.assignee.display_name or ticket.assignee.email},</p>
        <p>The following ticket has <strong>breached its SLA deadline</strong>:</p>
        <ul>
          <li><strong>Ticket:</strong> #{ticket.pk:04d} — {ticket.title}</li>
          <li><strong>Requester:</strong> {ticket.requester_name} ({ticket.requester_email})</li>
          <li><strong>SLA Deadline:</strong> {ticket.sla_deadline.strftime('%Y-%m-%d %H:%M') if ticket.sla_deadline else 'N/A'}</li>
        </ul>
        <p>Please take action immediately.</p>
        """
    )


def _send_sla_warning_email(ticket):
    _send_notification_email(
        to=ticket.assignee.email,
        subject=f'[Kdesk] SLA Warning — #{ticket.pk:04d}: {ticket.title}',
        body=f"""
        <p>Hello {ticket.assignee.display_name or ticket.assignee.email},</p>
        <p>The following ticket is approaching its SLA deadline ({ticket.sla_percent_elapsed}% elapsed):</p>
        <ul>
          <li><strong>Ticket:</strong> #{ticket.pk:04d} — {ticket.title}</li>
          <li><strong>SLA Deadline:</strong> {ticket.sla_deadline.strftime('%Y-%m-%d %H:%M') if ticket.sla_deadline else 'N/A'}</li>
        </ul>
        <p>Please respond soon.</p>
        """
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
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Kdesk] Your request has been received — #{ticket.pk:04d}',
        body=f"""
        <p>Hello {ticket.requester_name or ticket.requester_email},</p>
        <p>We have received your support request and assigned it ticket number <strong>#{ticket.pk:04d}</strong>.</p>
        <ul>
          <li><strong>Subject:</strong> {ticket.title}</li>
          <li><strong>Submitted:</strong> {ticket.created_at.strftime('%Y-%m-%d %H:%M') if ticket.created_at else 'N/A'}</li>
        </ul>
        <p>Our team will look into this and be in touch as soon as possible.<br>
        Please keep your ticket number for reference.</p>
        <p>Thank you,<br>IT Support Team</p>
        """,
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
    solution_html = (
        f'<p><strong>Resolution:</strong><br>{ticket.solution}</p>'
        if ticket.solution else ''
    )
    _send_notification_email(
        to=ticket.requester_email,
        subject=f'[Kdesk] Your ticket has been closed — #{ticket.pk:04d}',
        body=f"""
        <p>Hello {ticket.requester_name or ticket.requester_email},</p>
        <p>Your support ticket <strong>#{ticket.pk:04d}</strong> has been closed.</p>
        <ul>
          <li><strong>Subject:</strong> {ticket.title}</li>
          <li><strong>Closed:</strong> {ticket.resolved_at.strftime('%Y-%m-%d %H:%M') if ticket.resolved_at else 'N/A'}</li>
        </ul>
        {solution_html}
        <p>If you need further assistance, please submit a new request.</p>
        <p>Thank you,<br>IT Support Team</p>
        """,
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

    if event_type == 'assign' and ticket.assignee:
        _send_notification_email(
            to=ticket.assignee.email,
            subject=f'[Kdesk] Ticket Assigned — #{ticket.pk:04d}: {ticket.title}',
            body=f"""
            <p>Hello {ticket.assignee.display_name or ticket.assignee.email},</p>
            <p>A ticket has been assigned to you:</p>
            <ul>
              <li><strong>Ticket:</strong> #{ticket.pk:04d} — {ticket.title}</li>
              <li><strong>Requester:</strong> {ticket.requester_name} ({ticket.requester_email})</li>
              <li><strong>SLA Deadline:</strong> {ticket.sla_deadline.strftime('%Y-%m-%d %H:%M') if ticket.sla_deadline else 'N/A'}</li>
            </ul>
            """
        )

    elif event_type == 'update' and ticket.assignee:
        _send_notification_email(
            to=ticket.assignee.email,
            subject=f'[Kdesk] Ticket Updated — #{ticket.pk:04d}: {ticket.title}',
            body=f"""
            <p>Hello {ticket.assignee.display_name or ticket.assignee.email},</p>
            <p>Ticket #{ticket.pk:04d} was updated by <strong>{actor_name}</strong>.</p>
            <ul>
              <li><strong>Ticket:</strong> {ticket.title}</li>
              <li><strong>Status:</strong> {ticket.get_status_display()}</li>
            </ul>
            """
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

    logo_tag = '<span style="font-size:12px;font-weight:400;color:#aaaaaa;font-family:Segoe UI,Calibri,Arial,sans-serif;line-height:1.8;">Kramer</span>'

    body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f4f4f4;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:30px 0;">
        <tr><td align="center">
          <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

            <!-- Header -->
            <tr>
              <td style="background:#8200B4;padding:28px 36px;">
                <p style="margin:0;color:#ffffff;font-size:11px;letter-spacing:2px;text-transform:uppercase;opacity:0.8;font-family:'Segoe UI',Calibri,Arial,sans-serif;">IT Department</p>
                <h1 style="margin:6px 0 0;color:#ffffff;font-size:22px;font-weight:600;font-family:'Segoe UI',Calibri,Arial,sans-serif;">Planned Maintenance Notification</h1>
              </td>
            </tr>


            <!-- Body -->
            <tr>
              <td style="padding:32px 36px;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
                <p style="margin:0 0 20px;color:#333333;font-size:15px;line-height:1.6;">
                  Dear Employees,
                </p>
                <p style="margin:0 0 24px;color:#333333;font-size:15px;line-height:1.6;">
                  Please be informed that the IT Department has scheduled a <strong>Planned Maintenance</strong>
                  window. During this time, the affected system may be temporarily unavailable.
                </p>

                <!-- Details box -->
                <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f0ff;border-left:4px solid #8200B4;border-radius:4px;">
                  <tr>
                    <td style="padding:20px 24px;">
                      <table width="100%" cellpadding="6" cellspacing="0" style="font-size:14px;color:#333333;font-family:'Segoe UI',Calibri,Arial,sans-serif;">
                        <tr>
                          <td style="color:#8200B4;font-weight:600;white-space:nowrap;width:130px;">System</td>
                          <td style="color:#333333;">{system_str}</td>
                        </tr>
                        <tr>
                          <td style="color:#8200B4;font-weight:600;">Date</td>
                          <td style="color:#333333;">{date_str}</td>
                        </tr>
                        <tr>
                          <td style="color:#8200B4;font-weight:600;">Timeframe</td>
                          <td style="color:#333333;">{timeframe_str}</td>
                        </tr>
                        <tr>
                          <td style="color:#8200B4;font-weight:600;">Region</td>
                          <td style="color:#333333;">{region_str}</td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>

                <p style="margin:24px 0 12px;color:#333333;font-size:15px;line-height:1.6;">
                  We apologize for any inconvenience and will work to minimize disruption.
                  The system will be restored as quickly as possible.
                </p>
                <p style="margin:0;color:#333333;font-size:15px;line-height:1.6;">
                  If you have any questions, please contact the IT Support Team at
                  <a href="mailto:servicedesk@kramerav.com" style="color:#8200B4;">servicedesk@kramerav.com</a>.
                </p>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td bgcolor="#1a1a2e" style="background:#1a1a2e;padding:24px 36px;text-align:left;">
                {logo_tag}
                <span style="display:block;margin-top:10px;color:#aaaaaa;font-size:12px;line-height:1.8;font-family:'Segoe UI',Calibri,Arial,sans-serif;text-align:left;">
                  IT Support Team<br>
                  <a href="mailto:servicedesk@kramerav.com" style="color:#cc66ff;text-decoration:none;">servicedesk@kramerav.com</a>
                </span>
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """

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

IT_MANAGER_EMAIL = 'rlisbon@kramerav.com'


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

    if event == 'submitted':
        body = f"""
        <p>A new change request has been submitted and is awaiting your approval:</p>
        <ul>
          <li><strong>Change:</strong> #{change.pk:04d} — {change.title}</li>
          <li><strong>Risk Level:</strong> {change.get_risk_level_display()}</li>
          <li><strong>Affected System:</strong> {change.affected_system_display}</li>
          <li><strong>Planned Date:</strong> {planned}</li>
          <li><strong>Submitted By:</strong> {submitter_name}</li>
        </ul>
        <p><a href="{change_url}" style="display:inline-block;padding:10px 20px;background:#8200B4;color:#fff;text-decoration:none;border-radius:6px;">Review &amp; Approve in Kdesk</a></p>
        """
        _send_notification_email(
            to=IT_MANAGER_EMAIL,
            subject=f'[Kdesk] Change Request Pending Approval — #{change.pk:04d}: {change.title}',
            body=body,
        )
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Submitted — #{change.pk:04d}: {change.title}',
                body=f"""
                <p>Hello {submitter_name},</p>
                <p>Your change request <strong>#{change.pk:04d}</strong> has been submitted and is now pending approval by the IT Manager.</p>
                <ul>
                  <li><strong>Title:</strong> {change.title}</li>
                  <li><strong>Planned Date:</strong> {planned}</li>
                </ul>
                <p><a href="{change_url}">View change in Kdesk</a></p>
                <p>You will be notified when it is approved.</p>
                """,
            )

    elif event == 'approved':
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Approved — #{change.pk:04d}: {change.title}',
                body=f"""
                <p>Hello {submitter_name},</p>
                <p>Your change request <strong>#{change.pk:04d}</strong> has been <strong>approved</strong>.</p>
                <ul>
                  <li><strong>Title:</strong> {change.title}</li>
                  <li><strong>Planned Date:</strong> {planned}</li>
                </ul>
                <p>You may now proceed with implementation.</p>
                <p><a href="{change_url}">View change in Kdesk</a></p>
                """,
            )
        # Broadcast maintenance announcement to all affected employees
        _send_maintenance_announcement(change)

    elif event == 'done':
        body = f"""
        <p>The following change has been completed:</p>
        <ul>
          <li><strong>Change:</strong> #{change.pk:04d} — {change.title}</li>
          <li><strong>Risk Level:</strong> {change.get_risk_level_display()}</li>
          <li><strong>Affected System:</strong> {change.affected_system_display}</li>
          <li><strong>Implemented By:</strong> {submitter_name}</li>
        </ul>
        """
        _send_notification_email(
            to=IT_MANAGER_EMAIL,
            subject=f'[Kdesk] Change Completed — #{change.pk:04d}: {change.title}',
            body=body,
        )
        if submitter_email:
            _send_notification_email(
                to=submitter_email,
                subject=f'[Kdesk] Change Completed — #{change.pk:04d}: {change.title}',
                body=f"""
                <p>Hello {submitter_name},</p>
                <p>Change <strong>#{change.pk:04d} — {change.title}</strong> has been marked as <strong>Done</strong>.</p>
                """,
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

    if reminder_type == 'start':
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as In Progress'
        action_label = 'Mark as In Progress'
        message = (
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'has started ({timeframe}). Please remember to mark the change as <strong>In Progress</strong> '
            f'in Kdesk so the team knows the work has begun.'
        )
    elif reminder_type == 'done_followup':
        subject = f'[Kdesk] Action Required: Change #{change.pk:04d} Still Not Closed'
        action_label = 'Mark as Done'
        message = (
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'ended over an hour ago ({timeframe}), but the change has not been marked as <strong>Done</strong> yet. '
            f'Please update the status in Kdesk as soon as the work is complete.'
        )
    else:
        subject = f'[Kdesk] Reminder: Mark Change #{change.pk:04d} as Done'
        action_label = 'Mark as Done'
        message = (
            f'The planned maintenance window for <strong>{change.affected_system_display}</strong> '
            f'has ended ({timeframe}). Please remember to mark the change as <strong>Done</strong> '
            f'in Kdesk once the work is complete.'
        )

    body = f"""
    <p>Hello {submitter_name},</p>
    <p>{message}</p>
    <table style="border-left:3px solid #8200B4;padding:12px 20px;background:#f8f0ff;border-radius:4px;margin:20px 0;">
      <tr><td style="color:#8200B4;font-weight:700;padding:3px 16px 3px 0;white-space:nowrap;">Change</td>
          <td>#{change.pk:04d} — {change.title}</td></tr>
      <tr><td style="color:#8200B4;font-weight:700;padding:3px 16px 3px 0;">System</td>
          <td>{change.affected_system_display}</td></tr>
      <tr><td style="color:#8200B4;font-weight:700;padding:3px 16px 3px 0;">Date</td>
          <td>{date_str}</td></tr>
      <tr><td style="color:#8200B4;font-weight:700;padding:3px 16px 3px 0;">Timeframe</td>
          <td>{timeframe}</td></tr>
    </table>
    <p>
      <a href="{change_url}" style="display:inline-block;padding:10px 22px;background:#8200B4;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;">
        {action_label} in Kdesk
      </a>
    </p>
    <p style="color:#888;font-size:13px;">IT Support Team · servicedesk@kramerav.com</p>
    """

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
        ('Sync Entra Admins', 'tasks.sync_admins', weekly_interval),
        ('Check SLA', 'tasks.check_sla', sla_interval),
        ('Check Change Reminders', 'tasks.check_change_reminders', sla_interval),
    ]

    for name, task_name, schedule in tasks:
        PeriodicTask.objects.get_or_create(
            name=name,
            defaults={'task': task_name, 'interval': schedule, 'enabled': True},
        )
