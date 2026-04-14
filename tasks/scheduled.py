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
    """
    from tickets.models import Ticket
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
    planned = change.planned_date.strftime('%Y-%m-%d %H:%M') if change.planned_date else 'N/A'

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


# ── Setup scheduled tasks in the DB ──────────────────────────────────────────

def register_periodic_tasks():
    """
    Called from a management command on first run to seed the Celery Beat schedule.
    """
    from django_celery_beat.models import PeriodicTask, IntervalSchedule

    poll_interval, _ = IntervalSchedule.objects.get_or_create(
        every=settings.EMAIL_POLL_INTERVAL,
        period=IntervalSchedule.MINUTES,
    )
    sync_interval, _ = IntervalSchedule.objects.get_or_create(every=60, period=IntervalSchedule.MINUTES)
    weekly_interval, _ = IntervalSchedule.objects.get_or_create(every=10080, period=IntervalSchedule.MINUTES)
    sla_interval, _ = IntervalSchedule.objects.get_or_create(every=15, period=IntervalSchedule.MINUTES)

    tasks = [
        ('Poll Mailbox', 'tasks.poll_mailbox', poll_interval),
        ('Sync Entra Users', 'tasks.sync_users', sync_interval),
        ('Sync Entra Admins', 'tasks.sync_admins', weekly_interval),
        ('Check SLA', 'tasks.check_sla', sla_interval),
    ]

    for name, task_name, schedule in tasks:
        PeriodicTask.objects.get_or_create(
            name=name,
            defaults={'task': task_name, 'interval': schedule, 'enabled': True},
        )
