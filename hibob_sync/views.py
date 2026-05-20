import json
import logging
from collections import defaultdict

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .log_parser import parse_log
from .models import ProvisioningRequest, ProvisioningSettings, SyncChange, SyncRun, SyncTrigger

logger = logging.getLogger(__name__)


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
    recent_provisioning = ProvisioningRequest.objects.select_related('ticket').all()[:20]
    pending_provisioning_count = ProvisioningRequest.objects.filter(status__in=['pending', 'claimed']).count()

    return render(request, 'hibob_sync/dashboard.html', {
        'last_run': last_run,
        'active_trigger': active_trigger,
        'recent_runs': recent_runs,
        'changes_by_user': changes_by_user,
        'prov_settings': prov_settings,
        'recent_provisioning': recent_provisioning,
        'pending_provisioning_count': pending_provisioning_count,
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
        data = json.loads(request.body)
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
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    req_id = data.get('req_id')
    success = data.get('success', True)
    result_log = data.get('log', '')
    result_message = data.get('message', '')
    work_email = data.get('work_email', '')

    now = timezone.now()
    updated = ProvisioningRequest.objects.filter(id=req_id, status='claimed').update(
        status='completed' if success else 'failed',
        completed_at=now,
        result_success=success,
        result_log=result_log,
        result_message=result_message,
        work_email=work_email,
    )
    if not updated:
        return JsonResponse({'error': 'Request not found or not in claimed state'}, status=409)

    if success and work_email:
        _post_provisioning_ticket_comment(req_id, work_email, result_log)

    return JsonResponse({'ok': True})


def _post_provisioning_ticket_comment(req_id, work_email, log):
    try:
        req = ProvisioningRequest.objects.select_related('ticket').get(id=req_id)
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
