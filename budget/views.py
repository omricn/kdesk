import json
import logging

import requests as req
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

logger = logging.getLogger(__name__)


def budget_view(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    from .models import BudgetConfig
    config = BudgetConfig.get()

    # ── Save URL ──────────────────────────────────────────────────────────────
    if request.method == 'POST' and request.POST.get('action') == 'configure':
        url = request.POST.get('sharepoint_url', '').strip()
        config.sharepoint_url = url
        config.cached_sheets = ''
        config.cache_updated_at = None
        config.configured_by = request.user
        config.save()
        if url:
            from .tasks import refresh_budget_cache
            refresh_budget_cache.delay(config.pk)
            messages.success(request, 'SharePoint URL saved — loading data in background…')
        return redirect('budget')

    # ── Force refresh ─────────────────────────────────────────────────────────
    if request.method == 'POST' and request.POST.get('action') == 'refresh':
        config.cached_sheets = ''
        config.cache_updated_at = None
        config.save(update_fields=['cached_sheets', 'cache_updated_at'])
        from .tasks import refresh_budget_cache
        refresh_budget_cache.delay(config.pk)
        messages.success(request, 'Refresh started — this page will update automatically.')
        return redirect('budget')

    # ── Trigger background fetch if cache is stale (but don't block) ─────────
    if config.sharepoint_url and not config.cached_sheets and not config.cache_updated_at:
        # First-ever load with no task yet dispatched — kick one off
        from .tasks import refresh_budget_cache
        refresh_budget_cache.delay(config.pk)

    # ── Read from cache ───────────────────────────────────────────────────────
    sheets = []
    dashboard = None
    loading = False
    error = None

    if config.sharepoint_url and not config.cached_sheets:
        loading = True  # task is in-flight; page will auto-refresh
    elif config.cached_sheets:
        try:
            data = json.loads(config.cached_sheets)
            if isinstance(data, dict) and '_error' in data:
                error = data['_error']
            elif isinstance(data, list):
                sheets = data
                for s in sheets:
                    if s.get('name') == 'IT' and s.get('dashboard'):
                        dashboard = s['dashboard']
                        break
        except Exception:
            pass

    return render(request, 'budget/budget.html', {
        'config': config,
        'sheets': sheets,
        'dashboard': dashboard,
        'loading': loading,
        'error': error,
    })


def budget_excel_proxy(request):
    """Proxy the raw Excel file bytes through Kdesk so SheetJS can render it
    without hitting SharePoint's X-Frame-Options / CORS restrictions."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return HttpResponse(status=403)

    from .models import BudgetConfig
    from .graph import _encode_url, GRAPH
    from users.views import get_user_graph_token

    config = BudgetConfig.get()
    if not config.sharepoint_url:
        return HttpResponse(status=404)

    token = get_user_graph_token(request)
    if not token:
        return HttpResponse(status=401)

    try:
        hdrs = {'Authorization': f'Bearer {token}'}
        item = req.get(
            f'{GRAPH}/shares/{_encode_url(config.sharepoint_url)}/driveItem',
            headers=hdrs,
            params={'$select': 'id,parentReference'},
            timeout=15,
        )
        item.raise_for_status()
        item_data = item.json()
        drive_id = item_data['parentReference']['driveId']
        item_id = item_data['id']

        file_resp = req.get(
            f'{GRAPH}/drives/{drive_id}/items/{item_id}/content',
            headers=hdrs,
            timeout=60,
            allow_redirects=True,
        )
        file_resp.raise_for_status()

        return HttpResponse(
            file_resp.content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Cache-Control': 'no-store'},
        )
    except Exception as exc:
        logger.exception('Budget Excel proxy failed')
        return HttpResponse(str(exc), status=502)
