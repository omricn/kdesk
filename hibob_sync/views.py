import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .flow_parser import parse_offboarding_flow, parse_provisioning_flow
from .log_parser import parse_log
from .models import OffboardingRequest, OffboardingSettings, ProvisioningRequest, ProvisioningSettings, SyncChange, SyncRun, SyncTrigger

logger = logging.getLogger(__name__)


def _attach_flow(requests, parser):
    """Parse each request's result_log and attach `.flow` (FlowResult) and a
    `.flow_status` badge value (ok | warning | failed | '' when no info yet)."""
    for r in requests:
        r.flow = parser(r.result_log or '')
        if r.flow.overall != 'unknown':
            r.flow_status = r.flow.overall
        elif r.result_success is True:
            r.flow_status = 'ok'
        elif r.result_success is False:
            r.flow_status = 'failed'
        else:
            r.flow_status = ''
    return requests


def _superuser_required(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    return None


def _check_api_key(request):
    expected = getattr(settings, 'HIBOB_SYNC_API_KEY', '')
    if not expected:
        return False
    return request.headers.get('X-Sync-Api-Key') == expected


def _parse_json_body(request):
    """Parse a JSON request body, tolerating non-UTF-8 encodings.

    The on-prem PowerShell 5.1 agent's Invoke-WebRequest can send the body as
    Latin-1 rather than UTF-8 — e.g. an accented name like 'Díaz' becomes byte
    0xED, which raises UnicodeDecodeError under strict UTF-8 and 500s the
    endpoint, leaving the provisioning run stuck. Fall back to Latin-1 so a
    result report is never lost to an encoding mismatch. Raises
    json.JSONDecodeError for genuinely malformed JSON (callers return 400).
    """
    raw = request.body
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    return json.loads(text)


# ── UI Views ──────────────────────────────────────────────────────────────────

def hibob_sync_dashboard(request):
    deny = _superuser_required(request)
    if deny:
        return deny

    last_run = SyncRun.objects.prefetch_related('changes').first()
    active_trigger = SyncTrigger.objects.filter(status__in=['pending', 'running']).order_by('created_at').first()
    recent_runs = SyncRun.objects.select_related('trigger', 'trigger__triggered_by').all()[:10]

    changes_by_user = []
    if last_run and last_run.is_dry_run:
        grouped = defaultdict(list)
        for c in last_run.changes.all():
            grouped[c.email].append(c)
        changes_by_user = sorted(grouped.items())

    prov_settings = ProvisioningSettings.get()
    recent_provisioning = _attach_flow(
        list(ProvisioningRequest.objects.select_related('ticket').all()[:20]),
        parse_provisioning_flow,
    )
    # Attach the most recent Sentinel verification to each provisioning request (for the badge).
    for pr in recent_provisioning:
        pr.latest_verification = pr.verifications.first()   # model Meta ordering = -created_at
    pending_provisioning_count = ProvisioningRequest.objects.filter(
        status__in=['pending', 'claimed', 'review_needed', 'paused']
    ).count()
    active_provisioning = ProvisioningRequest.objects.filter(status='claimed').first()

    # Stuck: any request that has been in 'claimed' for more than 15 minutes
    stuck_threshold = timezone.now() - timedelta(minutes=15)
    stuck_provisioning = ProvisioningRequest.objects.filter(
        status='claimed',
        claimed_at__lt=stuck_threshold,
    ).first()

    offboard_settings = OffboardingSettings.get()
    recent_offboarding = _attach_flow(
        list(OffboardingRequest.objects.select_related('ticket').all()[:20]),
        parse_offboarding_flow,
    )
    # Attach the most recent Sentinel verification to each offboarding request (for the badge).
    for ob in recent_offboarding:
        ob.latest_verification = ob.verifications.first()   # model Meta ordering = -created_at
    pending_offboarding_count = OffboardingRequest.objects.filter(
        status__in=['pending', 'claimed', 'review_needed'],
    ).count()
    stuck_offboarding = OffboardingRequest.objects.filter(
        status='claimed',
        claimed_at__lt=timezone.now() - timedelta(minutes=15),
    ).first()

    return render(request, 'hibob_sync/dashboard.html', {
        'last_run': last_run,
        'active_trigger': active_trigger,
        'recent_runs': recent_runs,
        'changes_by_user': changes_by_user,
        'prov_settings': prov_settings,
        'recent_provisioning': recent_provisioning,
        'pending_provisioning_count': pending_provisioning_count,
        'active_provisioning': active_provisioning,
        'stuck_provisioning': stuck_provisioning,
        'offboard_settings': offboard_settings,
        'recent_offboarding': recent_offboarding,
        'pending_offboarding_count': pending_offboarding_count,
        'stuck_offboarding': stuck_offboarding,
        'now': timezone.now(),
    })


@require_POST
def hibob_sync_provisioning_toggle(request):
    deny = _superuser_required(request)
    if deny:
        return deny

    prov_settings = ProvisioningSettings.get()
    prov_settings.enabled = not prov_settings.enabled
    prov_settings.updated_by = request.user
    prov_settings.save()

    state = 'enabled' if prov_settings.enabled else 'disabled'
    messages.success(request, f'New employee provisioning {state}.')
    return redirect('hibob_sync_dashboard')


@require_POST
def hibob_sync_cancel(request, trigger_id):
    deny = _superuser_required(request)
    if deny:
        return deny

    updated = SyncTrigger.objects.filter(id=trigger_id, status='pending').update(
        status='failed',
        completed_at=timezone.now(),
    )
    if updated:
        messages.success(request, 'Sync cancelled.')
    else:
        messages.warning(request, 'Could not cancel — sync may have already started.')
    return redirect('hibob_sync_dashboard')


@require_POST
def hibob_sync_trigger(request):
    deny = _superuser_required(request)
    if deny:
        return deny

    if SyncTrigger.objects.filter(status__in=['pending', 'running']).exists():
        messages.warning(request, 'A sync is already queued or running.')
        return redirect('hibob_sync_dashboard')

    mode = request.POST.get('mode', 'dry')
    is_dry_run = mode != 'live'

    SyncTrigger.objects.create(is_dry_run=is_dry_run, triggered_by=request.user)

    kind = 'Dry run' if is_dry_run else 'Live sync'
    messages.success(request, f'{kind} queued — the agent will pick it up within 60 seconds.')
    return redirect('hibob_sync_dashboard')


def hibob_sync_log(request, run_id):
    deny = _superuser_required(request)
    if deny:
        return deny

    run = get_object_or_404(SyncRun, id=run_id)
    return HttpResponse(run.raw_log, content_type='text/plain; charset=utf-8')


def api_provisioning_statuses(request):
    """Lightweight JSON endpoint for live dashboard polling (UI only, not agent API)."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    recent = ProvisioningRequest.objects.all()[:20]
    return JsonResponse({
        'requests': [
            {
                'id': r.id,
                'status': r.status,
                'claimed_at': r.claimed_at.isoformat() if r.claimed_at else None,
            }
            for r in recent
        ]
    })


def provisioning_log(request, req_id):
    """Return the raw PS log stored for a provisioning request as plain text."""
    deny = _superuser_required(request)
    if deny:
        return deny

    req = get_object_or_404(ProvisioningRequest, id=req_id)
    content = req.result_log or '(no log available for this provisioning request)'
    return HttpResponse(content, content_type='text/plain; charset=utf-8')


# ── Agent API Views ───────────────────────────────────────────────────────────

@csrf_exempt
def api_pending(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    trigger = SyncTrigger.objects.filter(status='pending').order_by('created_at').first()
    if not trigger:
        return JsonResponse({'none': True}, status=404)

    return JsonResponse({'id': trigger.id, 'is_dry_run': trigger.is_dry_run})


@csrf_exempt
def api_claim(request, trigger_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    updated = SyncTrigger.objects.filter(id=trigger_id, status='pending').update(
        status='running',
        claimed_at=timezone.now(),
    )
    if not updated:
        return JsonResponse({'error': 'Already claimed or not found'}, status=409)

    return JsonResponse({'ok': True})


@csrf_exempt
def api_report(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        data = _parse_json_body(request)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    trigger_id = data.get('trigger_id')
    log_content = data.get('log', '')
    success = data.get('success', True)
    error_message = data.get('error_message', '')
    log_filename = data.get('log_filename', '')

    trigger = SyncTrigger.objects.filter(id=trigger_id).first()

    parsed = parse_log(log_content)

    now = timezone.now()

    def _make_aware(dt):
        if dt is None:
            return now
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    run = SyncRun.objects.create(
        trigger=trigger,
        started_at=_make_aware(parsed['started_at']),
        completed_at=_make_aware(parsed['completed_at']),
        is_dry_run=parsed['is_dry_run'] if parsed['started_at'] else (trigger.is_dry_run if trigger else False),
        matched=parsed['matched'],
        updated=parsed['updated'],
        skipped=parsed['skipped'],
        not_found=parsed['not_found'],
        errors=parsed['errors'],
        raw_log=log_content,
        success=success,
        error_message=error_message,
        log_filename=log_filename,
    )

    if parsed['changes']:
        SyncChange.objects.bulk_create([
            SyncChange(
                run=run,
                email=c['email'],
                field_name=c['field'],
                old_value=c['old'],
                new_value=c['new'],
            )
            for c in parsed['changes']
        ])

    if trigger:
        trigger.status = 'completed' if success else 'failed'
        trigger.completed_at = now
        trigger.save(update_fields=['status', 'completed_at'])

    return JsonResponse({'ok': True, 'run_id': run.id})


# ── Provisioning API Views ────────────────────────────────────────────────────

@csrf_exempt
def api_provisioning_pending(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    if not ProvisioningSettings.get().enabled:
        return JsonResponse({'none': True}, status=404)

    req = ProvisioningRequest.objects.filter(status='pending').order_by('created_at').first()
    if not req:
        return JsonResponse({'none': True}, status=404)

    return JsonResponse({
        'id': req.id,
        'first_name': req.first_name,
        'last_name': req.last_name,
        'middle_name': req.middle_name,
        'department': req.department,
        'division': req.division,
        'country': req.country,
        'region': req.region,
        'start_date': req.start_date.isoformat() if req.start_date else '',
        'personal_mobile': req.personal_mobile,
        'reports_to': req.reports_to,
        'job_title': req.job_title,
        'employment_type': req.employment_type,
        'employee_id': req.employee_id,
        'm365_groups': req.m365_groups,
        'groups_fallback': req.groups_fallback,
        'is_dry_run': req.is_dry_run,
        'force_create': req.force_create,
    })


@csrf_exempt
def api_provisioning_data(request, req_id):
    """Return full data for a claimed provisioning request (called by the PS script after claiming)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    req = ProvisioningRequest.objects.filter(id=req_id, status='claimed').first()
    if not req:
        return JsonResponse({'error': 'Request not found or not in claimed state'}, status=404)

    return JsonResponse({
        'id': req.id,
        'first_name': req.first_name,
        'last_name': req.last_name,
        'middle_name': req.middle_name,
        'department': req.department,
        'division': req.division,
        'country': req.country,
        'region': req.region,
        'start_date': req.start_date.isoformat() if req.start_date else '',
        'personal_mobile': req.personal_mobile,
        'reports_to': req.reports_to,
        'job_title': req.job_title,
        'employment_type': req.employment_type,
        'employee_id': req.employee_id,
        'm365_groups': req.m365_groups,
        'groups_fallback': req.groups_fallback,
        'is_dry_run': req.is_dry_run,
        'force_create': req.force_create,
    })


@csrf_exempt
def api_provisioning_claim(request, req_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    updated = ProvisioningRequest.objects.filter(id=req_id, status='pending').update(
        status='claimed',
        claimed_at=timezone.now(),
    )
    if not updated:
        return JsonResponse({'error': 'Already claimed or not found'}, status=409)

    return JsonResponse({'ok': True})


@csrf_exempt
def api_provisioning_report(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        data = _parse_json_body(request)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    req_id = data.get('req_id')
    if not req_id:
        return JsonResponse({'error': 'req_id is required'}, status=400)

    success = data.get('success', True)
    result_log = data.get('log', '')
    result_message = data.get('message', '')
    work_email = data.get('work_email', '')

    # Detect sentinels from the PowerShell script
    ACTIVE_USER_PREFIX   = 'ACTIVE_USER_FOUND:'
    DISABLED_USER_PREFIX = 'DISABLED_USER_FOUND:'
    is_active_user_blocked = isinstance(result_message, str) and result_message.startswith(ACTIVE_USER_PREFIX)
    is_disabled_user       = isinstance(result_message, str) and result_message.startswith(DISABLED_USER_PREFIX)

    if is_active_user_blocked:
        blocked_email = result_message[len(ACTIVE_USER_PREFIX):].strip()
        new_status = 'review_needed'
    elif is_disabled_user:
        blocked_email = result_message[len(DISABLED_USER_PREFIX):].strip()
        new_status = 'review_needed'
    else:
        blocked_email = ''
        new_status = 'completed' if success else 'failed'

    update_kwargs = {
        'status': new_status,
        'completed_at': timezone.now(),
        'result_success': success,
        'result_log': result_log,
        'result_message': result_message,
        'work_email': work_email,
    }
    if is_active_user_blocked or is_disabled_user:
        update_kwargs['blocked_by_email'] = blocked_email

    updated = ProvisioningRequest.objects.filter(id=req_id, status='claimed').update(**update_kwargs)
    if not updated:
        return JsonResponse({'error': 'Request not found or not in claimed state'}, status=409)

    # Post-report actions: fetch once, dispatch to helpers.
    # All exceptions are caught here so a downstream failure never prevents the 200 OK.
    try:
        req = ProvisioningRequest.objects.select_related('ticket').get(id=req_id)
        if is_active_user_blocked:
            _post_active_user_ticket_comment(req, blocked_email)
            _send_active_user_notification(req, blocked_email)
        elif is_disabled_user:
            _post_disabled_user_ticket_comment(req, blocked_email)
            _send_provisioning_result_notification(req, outcome='disabled', blocked_upn=blocked_email)
        elif success and work_email:
            _post_provisioning_ticket_comment(req, work_email, result_log)
            _create_system_tickets(req, work_email)
            _send_provisioning_result_notification(req, outcome='success', work_email=work_email)
            from tasks.scheduled import run_sentinel_verification
            run_sentinel_verification.apply_async(('provisioning', req.id), countdown=120)
        else:
            # Script reported failure (not a sentinel)
            _send_provisioning_result_notification(
                req, outcome='failed', failure_reason=result_message, result_log=result_log,
            )
    except Exception as exc:
        logger.error('[Provisioning] Post-report actions failed for req #%s: %s', req_id, exc)

    return JsonResponse({'ok': True})


def _post_provisioning_ticket_comment(req, work_email, log):
    try:
        if not req.ticket:
            return
        from tickets.models import TicketComment
        body = (
            f'AD account provisioned automatically.\n'
            f'Work email: {work_email}\n'
        )
        if req.groups_fallback:
            body += (
                '\nNote: No matching row found in the M365 group lookup table. '
                'Please assign department-specific groups manually.\n'
            )
        TicketComment.objects.create(
            ticket=req.ticket,
            author=None,
            body=body,
            is_internal=True,
        )
    except Exception as exc:
        logger.warning('[Provisioning] Could not post ticket comment: %s', exc)


def _create_system_tickets(req, work_email):
    """Create Priority and/or Salesforce tickets after successful provisioning."""
    from tickets.models import Ticket, TicketCategory, TicketSubCategory, TicketItem
    full_name = f'{req.first_name} {req.last_name}'.strip()

    # Resolve the new user's direct manager so admins' questions to the requester
    # also reach the manager. Prefer the email captured during provisioning; fall
    # back to a Graph lookup by display name. Never block ticket creation on this.
    manager_cc = (req.manager_email or '').strip()
    if not manager_cc and req.reports_to:
        manager_cc = _lookup_manager_email(req.reports_to) or ''
    if not manager_cc:
        logger.warning(
            '[Provisioning] Could not resolve direct-manager email for req #%s '
            '(reports_to=%r); system tickets created without manager CC.',
            req.id, req.reports_to,
        )

    systems = []
    if req.create_priority_ticket:
        extra_rows = []
        if req.priority_permissions_as:
            extra_rows.append(f'Priority Permissions as: {req.priority_permissions_as}')
        if req.salesforce_country_permission:
            extra_rows.append(f'Country Permission: {req.salesforce_country_permission}')
        systems.append({
            'system':     'Priority',
            'subcat':     'Priority',
            'item':       'New User',
            'extra_rows': extra_rows,
        })
    if req.create_salesforce_ticket:
        extra_rows = []
        if req.salesforce_permissions_as:
            extra_rows.append(f'Salesforce Permissions as: {req.salesforce_permissions_as}')
        systems.append({
            'system':     'Salesforce',
            'subcat':     'Salesforce',
            'item':       'New User',
            'extra_rows': extra_rows,
        })

    for s in systems:
        try:
            cat = TicketCategory.objects.get(name='IT')
            subcat = TicketSubCategory.objects.get(category=cat, name=s['subcat'])
            item, _ = TicketItem.objects.get_or_create(subcategory=subcat, name=s['item'])

            if Ticket.objects.filter(
                subcategory=subcat, requester_email__iexact=work_email,
                title__startswith='NEW USER',
            ).exists():
                logger.info('[Provisioning] %s ticket already exists for %s — skipping (idempotent).', s['system'], work_email)
                continue

            description = (
                f'New {s["system"]} user setup required.\n\n'
                f'First name: {req.first_name}\n'
                f'Last name: {req.last_name}\n'
                f'Email: {work_email}\n'
            )
            for row in s.get('extra_rows', []):
                description += f'{row}\n'

            ticket = Ticket(
                title=f'NEW USER – {s["system"]} – {full_name}',
                description=description,
                description_is_html=False,
                requester_email=work_email,
                requester_name=full_name,
                source=Ticket.SOURCE_MANUAL,
                category=cat,
                subcategory=subcat,
                ticket_item=item,
                assignee=subcat.assignee,
                email_cc=manager_cc,
            )
            ticket.save()
            logger.info('[Provisioning] Created %s ticket #%s for %s', s['system'], ticket.pk, work_email)
        except Exception as exc:
            logger.warning('[Provisioning] Could not create %s ticket: %s', s['system'], exc)


def _post_active_user_ticket_comment(req, blocked_email):
    """Post an internal ticket comment when provisioning is blocked by an active AD account."""
    try:
        if not req.ticket:
            return
        from tickets.models import TicketComment
        body = (
            f'⚠️ Provisioning blocked — an active AD account already exists.\n'
            f'Existing account: {blocked_email}\n\n'
            f'A superuser notification has been sent to Kdesk_Superusers@kramerav.com.\n'
            f'Options:\n'
            f'  • Continue Provisioning — if this is genuinely a different person with the same name.\n'
            f'  • Dismiss — if this email refers to the same person who is already active.\n'
        )
        TicketComment.objects.create(
            ticket=req.ticket,
            author=None,
            body=body,
            is_internal=True,
        )
    except Exception as exc:
        logger.warning('[Provisioning] Could not post active-user ticket comment: %s', exc)


def _post_disabled_user_ticket_comment(req, disabled_upn):
    """Post an internal ticket comment when a disabled AD account is found (returning employee)."""
    try:
        if not req.ticket:
            return
        from tickets.models import TicketComment
        body = (
            f'⚠️ Provisioning paused — a disabled AD account already exists for this employee.\n'
            f'Existing disabled account: {disabled_upn}\n\n'
            f'This may be a returning employee. Please re-activate the account manually in AD,\n'
            f'run an AD Connect delta sync, then close this provisioning request once complete.\n'
        )
        TicketComment.objects.create(
            ticket=req.ticket,
            author=None,
            body=body,
            is_internal=True,
        )
    except Exception as exc:
        logger.warning('[Provisioning] Could not post disabled-user ticket comment: %s', exc)


def _send_provisioning_result_notification(req, outcome='success', work_email='',
                                           failure_reason='', blocked_upn='', result_log=''):
    """
    Send a provisioning outcome email to Kdesk_Superusers.

    outcome: 'success' | 'failed' | 'disabled'
    result_log: full PS log text (included in failure emails as a highlighted tail).
    """
    try:
        from tasks.scheduled import _email_html, _row
        from integrations.graph_client import get_client
        from django.utils.html import escape

        full_name = f'{req.first_name} {req.last_name}'.strip()
        dashboard_url = 'https://kdesk.kramerav.com/hibob-sync/#tab-prov'
        ticket_url = (
            f'https://kdesk.kramerav.com/tickets/{req.ticket.pk}/'
            if req.ticket else None
        )
        dept_str = (
            f'{req.division} / {req.department}' if req.division and req.department
            else (req.division or req.department or '—')
        )

        if outcome == 'success':
            subject      = f'✅ Provisioned — {full_name}'
            header_color = '#28a745'
            header_title = 'New Employee Provisioned'
            greeting     = (f'Hi Kdesk Team,<br><br>The account for <strong>{escape(full_name)}</strong> '
                            f'has been successfully provisioned.')
        elif outcome == 'failed':
            subject      = f'❌ Provisioning FAILED — {full_name}'
            header_color = '#dc3545'
            header_title = 'Provisioning Failed'
            greeting     = (f'Hi Kdesk Team,<br><br>The provisioning script encountered an error '
                            f'while creating the account for <strong>{escape(full_name)}</strong>.')
        else:  # disabled
            subject      = f'⚠️ Disabled Account Found — {full_name}'
            header_color = '#fd7e14'
            header_title = 'Returning Employee — Manual Re-activation Required'
            greeting     = (f'Hi Kdesk Team,<br><br>A disabled AD account was found for '
                            f'<strong>{escape(full_name)}</strong>. Manual re-activation is required.')

        rows_html = (
            _row('Employee',   full_name, header_color) +
            _row('Job Title',  req.job_title or '—') +
            _row('Department', dept_str) +
            _row('Country',    req.country or '—') +
            _row('Start Date', str(req.start_date) if req.start_date else '—')
        )
        if outcome == 'success':
            rows_html += _row('Work Email', work_email)
        elif outcome == 'failed':
            rows_html += _row('Failure Reason', failure_reason or 'See log below.')
        else:
            rows_html += _row('Disabled Account', blocked_upn)

        extra_html = ''
        if outcome == 'failed' and result_log:
            all_lines = result_log.splitlines()
            tail = all_lines[-50:] if len(all_lines) > 50 else all_lines
            line_parts = []
            for line in tail:
                esc = escape(line)
                if '[ERROR]' in line:
                    line_parts.append(f'<span style="color:#dc3545;font-weight:bold;">{esc}</span>')
                elif '[WARN]' in line:
                    line_parts.append(f'<span style="color:#fd7e14;">{esc}</span>')
                else:
                    line_parts.append(esc)
            omitted = len(all_lines) - len(tail)
            omitted_note = (
                f'<div style="color:#888;font-size:11px;margin-bottom:4px;">'
                f'(first {omitted} lines omitted — '
                f'<a href="{dashboard_url}" style="color:#8205B4;">view full log in dashboard</a>)'
                f'</div>'
            ) if omitted else ''
            extra_html = (
                f'<div style="margin-top:8px;">'
                f'<div style="font-weight:bold;color:#555;margin-bottom:6px;font-size:13px;">Script Log</div>'
                f'{omitted_note}'
                f'<pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;'
                f'font-size:11px;line-height:1.5;overflow-x:auto;white-space:pre-wrap;'
                f'word-break:break-all;margin:0;">{chr(10).join(line_parts)}</pre>'
                f'</div>'
            )

        if ticket_url:
            extra_html += (
                f'<p style="margin:12px 0 0;font-size:13px;">'
                f'<a href="{ticket_url}" style="color:#8205B4;text-decoration:none;">View related ticket →</a>'
                f'</p>'
            )

        body_html = _email_html(
            header_title=header_title,
            header_subtitle=full_name,
            header_color=header_color,
            greeting=greeting,
            body_rows=rows_html,
            cta_url=dashboard_url,
            cta_label='View Dashboard',
            extra_html=extra_html,
        )

        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email='Kdesk_Superusers@kramerav.com',
            subject=subject,
            body_html=body_html,
        )
        logger.info(
            '[Provisioning] Result notification sent for req #%s (outcome=%s)', req.id, outcome
        )
    except Exception as exc:
        logger.warning('[Provisioning] Could not send result notification: %s', exc)


def _send_active_user_notification(req, blocked_email):
    """Send a branded Kramer notification email to Kdesk_Superusers when an active account is detected."""
    try:
        from django.template.loader import render_to_string
        from integrations.graph_client import get_client

        dashboard_url = 'https://kdesk.kramerav.com/hibob-sync/#tab-prov'
        ticket_url = None
        if req.ticket:
            ticket_url = f'https://kdesk.kramerav.com/tickets/{req.ticket.pk}/'

        body_html = render_to_string('hibob_sync/email_active_user_notification.html', {
            'employee_name': f'{req.first_name} {req.last_name}',
            'first_name': req.first_name,
            'last_name': req.last_name,
            'blocked_email': blocked_email,
            'department': req.department,
            'country': req.country,
            'job_title': req.job_title,
            'start_date': req.start_date,
            'dashboard_url': dashboard_url,
            'ticket_url': ticket_url,
            'req_id': req.id,
        })

        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email='Kdesk_Superusers@kramerav.com',
            subject=f'⚠️ Active Account Detected — {req.first_name} {req.last_name} Provisioning Needs Review',
            body_html=body_html,
        )
        logger.info('[Provisioning] Active-user notification sent for req #%s', req.id)
    except Exception as exc:
        logger.warning('[Provisioning] Could not send active-user notification email: %s', exc)


# ── Provisioning UI actions ───────────────────────────────────────────────────

@require_POST
def provisioning_requeue(request, req_id):
    """Re-queue a review_needed request with force_create=True so the agent skips the active-account check."""
    deny = _superuser_required(request)
    if deny:
        return deny

    updated = ProvisioningRequest.objects.filter(id=req_id, status='review_needed').update(
        status='pending',
        force_create=True,
        claimed_at=None,
        completed_at=None,
    )
    if updated:
        messages.success(request, 'Provisioning re-queued. The agent will create a new account shortly.')
    else:
        messages.warning(request, 'Could not re-queue — request may not be in review state.')
    return redirect('hibob_sync_dashboard')


@require_POST
def provisioning_retry(request, req_id):
    """Reset a stuck 'claimed' (or 'failed') request back to 'pending' so the agent
    re-picks it. Unlike requeue, this does NOT set force_create — the agent's
    active-account check still guards against duplicating a partially-created
    account, so it's safe whether or not the half-built account was cleaned up."""
    deny = _superuser_required(request)
    if deny:
        return deny

    updated = ProvisioningRequest.objects.filter(
        id=req_id, status__in=('claimed', 'failed'),
    ).update(
        status='pending',
        claimed_at=None,
        completed_at=None,
    )
    if updated:
        messages.success(request, 'Provisioning re-queued — the agent will retry it shortly.')
    else:
        messages.warning(request, 'Could not retry — request is not in a stuck/failed state.')
    return redirect('hibob_sync_dashboard')


@require_POST
def provisioning_cancel(request, req_id):
    """Cancel any active provisioning request — it will not be picked up again."""
    deny = _superuser_required(request)
    if deny:
        return deny

    CANCELLABLE = ('pending', 'paused', 'claimed', 'failed', 'review_needed')
    updated = ProvisioningRequest.objects.filter(id=req_id, status__in=CANCELLABLE).update(
        status='cancelled',
        completed_at=timezone.now(),
    )
    if updated:
        messages.success(request, 'Provisioning request cancelled.')
    else:
        messages.warning(request, 'Could not cancel — request may already be completed or cancelled.')
    return redirect('hibob_sync_dashboard')


@require_POST
def provisioning_pause(request, req_id):
    """Pause a pending request — it will stay visible but the agent will not pick it up."""
    deny = _superuser_required(request)
    if deny:
        return deny

    updated = ProvisioningRequest.objects.filter(id=req_id, status='pending').update(
        status='paused',
    )
    if updated:
        messages.success(request, 'Provisioning paused — the agent will skip this request until resumed.')
    else:
        messages.warning(request, 'Could not pause — request may not be in pending state.')
    return redirect('hibob_sync_dashboard')


@require_POST
def provisioning_resume(request, req_id):
    """Resume a paused request — puts it back in the pending queue."""
    deny = _superuser_required(request)
    if deny:
        return deny

    updated = ProvisioningRequest.objects.filter(id=req_id, status='paused').update(
        status='pending',
    )
    if updated:
        messages.success(request, 'Provisioning resumed — the agent will pick it up shortly.')
    else:
        messages.warning(request, 'Could not resume — request may not be paused.')
    return redirect('hibob_sync_dashboard')


@require_POST
def offboarding_manual_trigger(request):
    deny = _superuser_required(request)
    if deny:
        return deny

    employee_email = request.POST.get('employee_email', '').strip().lower()
    manager_name   = request.POST.get('manager_name', '').strip()

    if not employee_email:
        messages.error(request, 'Employee email is required.')
        return redirect('hibob_sync_dashboard')

    OffboardingRequest.objects.create(
        employee_email=employee_email,
        employee_name=request.POST.get('employee_name', '').strip(),
        direct_manager=manager_name,
        scheduled_for=timezone.now(),
        status='pending',
    )
    messages.success(
        request,
        f'Manual offboarding request created for {employee_email}. '
        f'The agent will pick it up within 60 seconds.',
    )
    return redirect('hibob_sync_dashboard')


@require_POST
def hibob_sync_offboarding_toggle(request):
    deny = _superuser_required(request)
    if deny:
        return deny

    offboard_settings = OffboardingSettings.get()
    offboard_settings.enabled = not offboard_settings.enabled
    offboard_settings.updated_by = request.user
    offboard_settings.save()

    state = 'enabled' if offboard_settings.enabled else 'disabled'
    messages.success(request, f'Employee offboarding {state}.')
    return redirect('hibob_sync_dashboard')


# ── Offboarding Agent API Views ───────────────────────────────────────────────

@csrf_exempt
def api_offboarding_pending(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    if not OffboardingSettings.get().enabled:
        return JsonResponse({'none': True}, status=404)

    now = timezone.now()
    req = OffboardingRequest.objects.filter(
        status='pending',
    ).filter(
        models.Q(scheduled_for__lte=now) | models.Q(scheduled_for__isnull=True),
    ).order_by('created_at').first()
    if not req:
        return JsonResponse({'none': True}, status=404)

    return JsonResponse({
        'id':              req.id,
        'employee_email':  req.employee_email,
        'employee_name':   req.employee_name,
        'direct_manager':  req.direct_manager,
        'country_origin':  req.country_origin,
        'termination_date': req.termination_date.isoformat() if req.termination_date else '',
    })


@csrf_exempt
def api_offboarding_claim(request, req_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    updated = OffboardingRequest.objects.filter(id=req_id, status='pending').update(
        status='claimed',
        claimed_at=timezone.now(),
    )
    if not updated:
        return JsonResponse({'error': 'Already claimed or not found'}, status=409)
    return JsonResponse({'ok': True})


@csrf_exempt
def api_offboarding_report(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        data = _parse_json_body(request)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    req_id = data.get('req_id')
    if not req_id:
        return JsonResponse({'error': 'req_id is required'}, status=400)

    success = data.get('success', True)
    result_log = data.get('log', '')
    result_message = data.get('message', '')

    EMPLOYEE_NOT_FOUND_PREFIX = 'EMPLOYEE_NOT_FOUND:'
    is_not_found = isinstance(result_message, str) and result_message.startswith(EMPLOYEE_NOT_FOUND_PREFIX)

    if is_not_found:
        new_status = 'review_needed'
    else:
        new_status = 'completed' if success else 'failed'

    updated = OffboardingRequest.objects.filter(id=req_id, status='claimed').update(
        status=new_status,
        completed_at=timezone.now(),
        result_success=success,
        result_log=result_log,
        result_message=result_message,
    )
    if not updated:
        return JsonResponse({'error': 'Request not found or not in claimed state'}, status=409)

    try:
        req = OffboardingRequest.objects.select_related('ticket').get(id=req_id)
        if is_not_found:
            _post_offboarding_ticket_comment(req, outcome='not_found')
            _send_offboarding_notification(req, outcome='not_found')
        elif success:
            _post_offboarding_ticket_comment(req, outcome='success')
            _send_offboarding_notification(req, outcome='success')
            _create_offboarding_system_tickets(req)
            _send_manager_onedrive_notification(req)
            from tasks.scheduled import run_sentinel_verification
            run_sentinel_verification.apply_async(('offboarding', req.id), countdown=120)
        else:
            _post_offboarding_ticket_comment(req, outcome='failed')
            _send_offboarding_notification(req, outcome='failed', result_log=result_log)
    except Exception as exc:
        logger.error('[Offboarding] Post-report actions failed for req #%s: %s', req_id, exc)

    return JsonResponse({'ok': True})


# ── Offboarding UI Views ──────────────────────────────────────────────────────

@require_POST
def offboarding_cancel(request, req_id):
    deny = _superuser_required(request)
    if deny:
        return deny

    CANCELLABLE = ('pending', 'claimed', 'failed', 'review_needed')
    updated = OffboardingRequest.objects.filter(id=req_id, status__in=CANCELLABLE).update(
        status='cancelled',
        completed_at=timezone.now(),
    )
    if updated:
        messages.success(request, 'Offboarding request cancelled.')
    else:
        messages.warning(request, 'Could not cancel — request may already be completed or cancelled.')
    return redirect('hibob_sync_dashboard')


def offboarding_log(request, req_id):
    deny = _superuser_required(request)
    if deny:
        return deny

    req = get_object_or_404(OffboardingRequest, id=req_id)
    content = req.result_log or '(no log available for this offboarding request)'
    return HttpResponse(content, content_type='text/plain; charset=utf-8')


def api_offboarding_statuses(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    recent = OffboardingRequest.objects.all()[:20]
    return JsonResponse({
        'requests': [
            {
                'id':         r.id,
                'status':     r.status,
                'claimed_at': r.claimed_at.isoformat() if r.claimed_at else None,
            }
            for r in recent
        ]
    })


# ── Offboarding helpers ───────────────────────────────────────────────────────

def _create_offboarding_system_tickets(req):
    """Create Priority and Salesforce termination tickets after successful offboarding."""
    from tickets.models import Ticket, TicketCategory, TicketSubCategory, TicketItem
    full_name = req.employee_name.strip() if req.employee_name else req.employee_email

    for system in ('Priority', 'Salesforce'):
        try:
            cat = TicketCategory.objects.get(name='IT')
            subcat = TicketSubCategory.objects.get(category=cat, name=system)
            item, _ = TicketItem.objects.get_or_create(subcategory=subcat, name='Terminate Employee')

            description = (
                f'Please terminate the {system} account for the following employee '
                f'if such an account exists.\n\n'
                f'Full name: {full_name}\n'
                f'Kramer email: {req.employee_email}\n'
            )

            ticket = Ticket(
                title=f'TERMINATE USER – {system} – {full_name}',
                description=description,
                description_is_html=False,
                requester_email=req.employee_email,
                requester_name=full_name,
                source=Ticket.SOURCE_MANUAL,
                category=cat,
                subcategory=subcat,
                ticket_item=item,
                assignee=subcat.assignee,
            )
            ticket.save()
            logger.info('[Offboarding] Created %s termination ticket #%s for %s', system, ticket.pk, req.employee_email)
        except Exception as exc:
            logger.warning('[Offboarding] Could not create %s termination ticket: %s', system, exc)


def _post_offboarding_ticket_comment(req, outcome='success'):
    try:
        if not req.ticket:
            return
        from tickets.models import TicketComment
        if outcome == 'success':
            body = (
                f'Employee offboarding completed automatically.\n'
                f'Account: {req.employee_email}\n'
                f'AD account disabled, moved to deletion OU. Mailbox converted to Shared.\n'
            )
        elif outcome == 'not_found':
            body = (
                f'Offboarding could not proceed — employee account not found in AD.\n'
                f'Searched by email: {req.employee_email}\n'
                f'Please verify the account manually and handle offboarding steps if needed.\n'
            )
        else:
            body = (
                f'Offboarding script failed for: {req.employee_email}\n'
                f'See the log in the Kdesk offboarding dashboard for details.\n'
            )
        TicketComment.objects.create(ticket=req.ticket, author=None, body=body, is_internal=True)
    except Exception as exc:
        logger.warning('[Offboarding] Could not post ticket comment: %s', exc)


def _send_offboarding_notification(req, outcome='success', result_log=''):
    try:
        from tasks.scheduled import _email_html, _row
        from integrations.graph_client import get_client
        from django.utils.html import escape

        dashboard_url = 'https://kdesk.kramerav.com/hibob-sync/#tab-offboard'
        ticket_url = (
            f'https://kdesk.kramerav.com/tickets/{req.ticket.pk}/'
            if req.ticket else None
        )
        display_name = escape(req.employee_name or req.employee_email)

        if outcome == 'success':
            subject      = f'Offboarded — {req.employee_name or req.employee_email}'
            header_color = '#28a745'
            header_title = 'Employee Offboarding Completed'
            greeting     = f'Hi Kdesk Team,<br><br>The offboarding of <strong>{display_name}</strong> has been completed successfully.'
        elif outcome == 'not_found':
            subject      = f'Offboarding Blocked — Employee Not Found in AD ({req.employee_email})'
            header_color = '#fd7e14'
            header_title = 'Offboarding Blocked — Employee Not Found'
            greeting     = (f'Hi Kdesk Team,<br><br>The AD account for <strong>{display_name}</strong> '
                            f'was not found by email address. Manual action may be required.')
        else:
            subject      = f'Offboarding FAILED — {req.employee_name or req.employee_email}'
            header_color = '#dc3545'
            header_title = 'Employee Offboarding Failed'
            greeting     = (f'Hi Kdesk Team,<br><br>The offboarding script encountered an error '
                            f'while processing <strong>{display_name}</strong>.')

        rows_html = (
            _row('Employee',         req.employee_name or '—', header_color) +
            _row('Email',            req.employee_email) +
            _row('Department',       req.department or '—') +
            _row('Manager',          req.direct_manager or '—') +
            _row('Country',          req.country_origin or '—') +
            _row('Termination Date', str(req.termination_date) if req.termination_date else '—')
        )

        extra_html = ''
        if outcome == 'not_found':
            extra_html = (
                '<div style="background:#fff3cd;border-left:4px solid #ffc107;border-radius:4px;'
                'padding:12px 16px;margin-top:8px;font-size:13px;color:#856404;">'
                '<strong>Action required:</strong> Please verify the account exists in AD and '
                'handle offboarding steps manually if needed.'
                '</div>'
            )
        elif outcome == 'failed' and result_log:
            all_lines = result_log.splitlines()
            tail = all_lines[-50:] if len(all_lines) > 50 else all_lines
            line_parts = []
            for line in tail:
                esc = escape(line)
                if '[ERROR]' in line:
                    line_parts.append(f'<span style="color:#dc3545;font-weight:bold;">{esc}</span>')
                elif '[WARN]' in line:
                    line_parts.append(f'<span style="color:#fd7e14;">{esc}</span>')
                else:
                    line_parts.append(esc)
            omitted = len(all_lines) - len(tail)
            omitted_note = (
                f'<div style="color:#888;font-size:11px;margin-bottom:4px;">'
                f'(first {omitted} lines omitted — '
                f'<a href="{dashboard_url}" style="color:#8205B4;">view full log in dashboard</a>)'
                f'</div>'
            ) if omitted else ''
            extra_html = (
                f'<div style="margin-top:8px;">'
                f'<div style="font-weight:bold;color:#555;margin-bottom:6px;font-size:13px;">Script Log</div>'
                f'{omitted_note}'
                f'<pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;'
                f'font-size:11px;line-height:1.5;overflow-x:auto;white-space:pre-wrap;'
                f'word-break:break-all;margin:0;">{chr(10).join(line_parts)}</pre>'
                f'</div>'
            )

        if ticket_url:
            extra_html += (
                f'<p style="margin:12px 0 0;font-size:13px;">'
                f'<a href="{ticket_url}" style="color:#8205B4;text-decoration:none;">View related ticket →</a>'
                f'</p>'
            )

        body_html = _email_html(
            header_title=header_title,
            header_subtitle=req.employee_name or req.employee_email,
            header_color=header_color,
            greeting=greeting,
            body_rows=rows_html,
            cta_url=dashboard_url,
            cta_label='View Dashboard',
            extra_html=extra_html,
        )

        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email='Kdesk_Superusers@kramerav.com',
            subject=subject,
            body_html=body_html,
        )
        logger.info('[Offboarding] Notification sent for req #%s (outcome=%s)', req.id, outcome)
    except Exception as exc:
        logger.warning('[Offboarding] Could not send notification: %s', exc)


def _onedrive_url_for(employee_email: str) -> str:
    slug = employee_email.lower().replace('@', '_').replace('.', '_')
    return f'https://kramer365-my.sharepoint.com/personal/{slug}/'


def _lookup_manager_email(manager_display_name: str) -> str | None:
    """Return the UPN/email for a manager given their display name, via Graph."""
    try:
        import urllib.parse
        from integrations.graph_client import get_client
        from django.conf import settings as _s
        import requests as _req

        token_url = f"https://login.microsoftonline.com/{_s.AZURE_TENANT_ID}/oauth2/v2.0/token"
        resp = _req.post(token_url, data={
            'client_id':     _s.AZURE_CLIENT_ID,
            'client_secret': _s.AZURE_CLIENT_SECRET,
            'scope':         'https://graph.microsoft.com/.default',
            'grant_type':    'client_credentials',
        }, timeout=15)
        token = resp.json()['access_token']
        name_q = urllib.parse.quote(manager_display_name)
        users = _req.get(
            f"https://graph.microsoft.com/v1.0/users"
            f"?$filter=displayName eq '{name_q}'"
            f"&$select=userPrincipalName,displayName",
            headers={'Authorization': f'Bearer {token}'},
            timeout=15,
        ).json().get('value', [])
        if users:
            return users[0]['userPrincipalName']
    except Exception as exc:
        logger.warning('[Offboarding] Manager email lookup failed for "%s": %s', manager_display_name, exc)
    return None


def _build_manager_onedrive_email_html(employee_name, employee_email, manager_name, onedrive_url):
    from tasks.scheduled import _email_html, _row
    from django.utils.html import escape

    font = "'Segoe UI', Calibri, Arial, Helvetica, sans-serif"
    onedrive_button = (
        '<table cellspacing="0" cellpadding="0" style="margin:0;"><tr><td align="left">'
        '<!--[if mso]>'
        f'<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" '
        f'xmlns:w="urn:schemas-microsoft-com:office:word" href="{onedrive_url}" '
        f'style="height:44px;v-text-anchor:middle;width:220px;" arcsize="11%" stroke="f" fillcolor="#0078d4">'
        f'<w:anchorlock/>'
        f'<center style="color:#ffffff;font-family:\'Segoe UI\',Calibri,Arial,sans-serif;'
        f'font-size:14px;font-weight:700;">Open OneDrive &rarr;</center>'
        f'</v:roundrect>'
        '<![endif]-->'
        '<!--[if !mso]><!-->'
        f'<a href="{onedrive_url}" style="background-color:#0078d4;border-radius:6px;'
        f'color:#ffffff;display:inline-block;font-family:{font};font-size:14px;font-weight:700;'
        f'line-height:44px;text-align:center;text-decoration:none;width:220px;'
        f'-webkit-text-size-adjust:none;mso-hide:all;">'
        f'Open OneDrive &rarr;</a>'
        '<!--<![endif]-->'
        '</td></tr></table>'
    )

    greeting = (
        f'Hi <strong>{escape(manager_name)}</strong>,<br><br>'
        f'The offboarding of <strong>{escape(employee_name)}</strong> ({escape(employee_email)}) '
        f'has been completed. As the direct manager, you have been granted access to their OneDrive.'
        f'<br><br>'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff8e1;'
        f'border-left:4px solid #e67e22;border-radius:4px;margin:0 0 8px;">'
        f'<tr><td style="padding:14px 18px;">'
        f'<p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#b7570a;">'
        f'&#9888;&#65039;  Important: You have a limited time window to retrieve data.</p>'
        f'<p style="margin:0;font-size:13px;color:#5d4037;line-height:1.6;">'
        f'Microsoft automatically <strong>permanently deletes</strong> a departed employee\'s OneDrive '
        f'<strong>93 days from the date of offboarding</strong>. '
        f'After that point, the data is <strong>unrecoverable</strong>.<br><br>'
        f'<strong>Please copy any files or folders you need to your own OneDrive before this deadline.</strong>'
        f'</p></td></tr></table>'
    )

    rows_html = (
        _row('Employee', employee_name, '#8205B4') +
        _row('Email',    employee_email)
    )

    return _email_html(
        header_title='Action Required — OneDrive Access Window',
        header_subtitle=f'{employee_name} — 93-day data retrieval window',
        header_color='#8205B4',
        greeting=greeting,
        body_rows=rows_html,
        cta_raw=onedrive_button,
    )


def _send_manager_credentials_email(req):
    """Send a 'credentials ready' notification to the new employee's manager."""
    try:
        from tasks.scheduled import _email_html
        from integrations.graph_client import get_client
        from django.utils.html import escape

        full_name = f'{req.first_name} {req.last_name}'.strip()
        credentials_url = f'https://kdesk.kramerav.com/hibob-sync/credentials/{req.id}/'
        manager_greeting = escape(req.reports_to or 'Manager')
        font = "'Segoe UI', Calibri, Arial, Helvetica, sans-serif"

        # VML button for Outlook + CSS for all other clients
        vml_button = (
            '<table cellspacing="0" cellpadding="0" style="margin:0 auto;"><tr><td align="center">'
            '<!--[if mso]>'
            f'<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" '
            f'xmlns:w="urn:schemas-microsoft-com:office:word" href="{credentials_url}" '
            f'style="height:52px;v-text-anchor:middle;width:340px;" arcsize="11%" stroke="f" fillcolor="#8205B4">'
            f'<w:anchorlock/>'
            f'<center style="color:#ffffff;font-family:\'Segoe UI\',Calibri,Arial,sans-serif;'
            f'font-size:15px;font-weight:700;">See {escape(full_name)}\'s credentials &rarr;</center>'
            f'</v:roundrect>'
            '<![endif]-->'
            '<!--[if !mso]><!-->'
            f'<a href="{credentials_url}" style="background-color:#8205B4;border-radius:6px;'
            f'color:#ffffff;display:inline-block;font-family:{font};font-size:15px;font-weight:700;'
            f'line-height:52px;text-align:center;text-decoration:none;width:340px;'
            f'-webkit-text-size-adjust:none;mso-hide:all;">'
            f'See {escape(full_name)}\'s credentials &rarr;</a>'
            '<!--<![endif]-->'
            '</td></tr></table>'
        )

        body_html = _email_html(
            header_title='New Employee Account Ready',
            header_subtitle=full_name,
            greeting=(
                f'Hi <strong>{manager_greeting}</strong>,<br><br>'
                f'The Kramer account for <strong>{escape(full_name)}</strong> '
                f'has been created and is ready to use.'
            ),
            body_rows='',
            cta_raw=vml_button,
            extra_html=(
                '<p style="font-size:12px;color:#888;margin:0;">'
                'You will need to sign in with your Kramer company account to view the password. '
                'This link is intended for the direct manager only.'
                '</p>'
            ),
        )

        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=req.manager_email,
            subject=f'New employee account ready — {full_name}',
            body_html=body_html,
        )
        logger.info(
            '[Provisioning] Credentials email sent to %s for req #%s', req.manager_email, req.id
        )
    except Exception as exc:
        logger.warning('[Provisioning] Could not send credentials email: %s', exc)


def _send_manager_onedrive_notification(req):
    """Send the 93-day OneDrive access window email to the direct manager."""
    try:
        if not req.direct_manager:
            logger.info('[Offboarding] No manager on req #%s — skipping OneDrive notification.', req.id)
            return

        manager_email = _lookup_manager_email(req.direct_manager)
        if not manager_email:
            logger.warning('[Offboarding] Could not resolve manager email for "%s" — skipping OneDrive notification.', req.direct_manager)
            return

        employee_name = req.employee_name or req.employee_email
        onedrive_url  = _onedrive_url_for(req.employee_email)
        body_html     = _build_manager_onedrive_email_html(
            employee_name, req.employee_email, req.direct_manager, onedrive_url,
        )

        from integrations.graph_client import get_client
        from django.conf import settings as _s
        get_client().send_email(
            from_mailbox=_s.SERVICEDESK_EMAIL,
            to_email=manager_email,
            subject=f'Action Required — OneDrive Access Window for {employee_name}',
            body_html=body_html,
        )
        req.onedrive_email_sent = True
        req.save(update_fields=['onedrive_email_sent'])
        logger.info('[Offboarding] OneDrive manager notification sent to %s for req #%s', manager_email, req.id)
    except Exception as exc:
        logger.warning('[Offboarding] Could not send OneDrive manager notification: %s', exc)


def offboarding_manager_email_preview(request):
    """Send a preview of the manager OneDrive notification to the logged-in user."""
    deny = _superuser_required(request)
    if deny:
        return deny

    req = OffboardingRequest.objects.filter(status='completed').order_by('-completed_at').first()
    if not req:
        from django.http import HttpResponse
        return HttpResponse('No completed offboarding requests found.', status=404)

    employee_name = req.employee_name or req.employee_email
    onedrive_url  = _onedrive_url_for(req.employee_email)
    body_html     = _build_manager_onedrive_email_html(
        employee_name, req.employee_email, req.direct_manager or 'Manager', onedrive_url,
    )

    from integrations.graph_client import get_client
    from django.conf import settings as _s
    get_client().send_email(
        from_mailbox=_s.SERVICEDESK_EMAIL,
        to_email=request.user.email,
        subject=f'[PREVIEW] Action Required — OneDrive Access Window for {employee_name}',
        body_html=body_html,
    )
    from django.http import HttpResponse
    return HttpResponse(f'Preview sent to {request.user.email}. Check your inbox.', status=200)


# ── Credentials sharing ───────────────────────────────────────────────────────

@csrf_exempt
def api_store_credentials(request, req_id):
    """Called by the PS script after E5 and Joiners groups are confirmed assigned.
    Stores the temp password + manager email, then sends the manager notification email.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        data = _parse_json_body(request)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    password = data.get('password', '').strip()
    manager_email = data.get('manager_email', '').strip()

    if not password or not manager_email:
        return JsonResponse({'error': 'password and manager_email are required'}, status=400)

    req = ProvisioningRequest.objects.filter(id=req_id, status='claimed').first()
    if not req:
        return JsonResponse({'error': 'Request not found or not in claimed state'}, status=404)

    req.temp_password = password
    req.manager_email = manager_email
    req.save(update_fields=['temp_password', 'manager_email'])

    _send_manager_credentials_email(req)

    return JsonResponse({'ok': True})


def provisioning_credentials(request, req_id):
    """SSO-protected page that shows a new employee's temporary password to their manager."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    req = get_object_or_404(ProvisioningRequest, id=req_id)

    is_authorized = (
        request.user.is_superuser or
        (request.user.email and req.manager_email and
         request.user.email.lower() == req.manager_email.lower())
    )
    if not is_authorized:
        messages.error(request, 'Access denied — this link is only accessible to the assigned manager.')
        return redirect('dashboard')

    return render(request, 'hibob_sync/credentials.html', {'req': req})


def provisioning_credentials_viewed(request, req_id):
    """AJAX endpoint — marks credentials as viewed when the manager ticks the checkbox."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    req = get_object_or_404(ProvisioningRequest, id=req_id)

    is_authorized = (
        request.user.is_superuser or
        (request.user.email and req.manager_email and
         request.user.email.lower() == req.manager_email.lower())
    )
    if not is_authorized:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    if not req.credentials_viewed:
        ProvisioningRequest.objects.filter(id=req_id).update(credentials_viewed=True)

    return JsonResponse({'ok': True})


def test_credentials_email(request):
    """Superuser-only: create a mock provisioning record and fire the credentials email to yourself."""
    deny = _superuser_required(request)
    if deny:
        return deny

    req = ProvisioningRequest.objects.create(
        first_name='Test',
        last_name='NewUser',
        department='IT',
        division='Technology',
        country='Israel',
        region='HQ',
        reports_to=request.user.email,
        job_title='Test Account — delete me',
        status='completed',
        work_email='test.newuser@kramerav.com',
        temp_password='Tu12341234!@',
        manager_email=request.user.email,
    )
    _send_manager_credentials_email(req)

    from django.http import HttpResponse
    creds_url = f'https://kdesk.kramerav.com/hibob-sync/credentials/{req.id}/'
    return HttpResponse(
        f'Done. Email sent to {request.user.email}.<br>'
        f'req_id={req.id}<br>'
        f'<a href="{creds_url}">{creds_url}</a>',
        content_type='text/html',
    )
