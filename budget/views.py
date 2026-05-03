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
        messages.success(request, 'SharePoint URL saved — loading data…')
        return redirect('budget')

    # ── Force refresh ─────────────────────────────────────────────────────────
    if request.method == 'POST' and request.POST.get('action') == 'refresh':
        config.cached_sheets = ''
        config.cache_updated_at = None
        config.save(update_fields=['cached_sheets', 'cache_updated_at'])
        return redirect('budget')

    # ── Fetch from SharePoint (if cache stale) ────────────────────────────────
    error = None
    needs_relogin = False

    if config.sharepoint_url and not config.cache_is_fresh():
        from users.views import get_user_graph_token
        user_token = get_user_graph_token(request)

        if user_token is None:
            # Token missing — user logged in before Sites.Read.All was added
            needs_relogin = True
        else:
            try:
                from .graph import fetch_sheets_html
                result = fetch_sheets_html(config.sharepoint_url, token=user_token)
                sheets = result['sheets']
                if result.get('web_url'):
                    config.web_url = result['web_url']
                if result.get('embed_url'):
                    config.embed_url = result['embed_url']
                config.cached_sheets = json.dumps(sheets)
                if sheets:
                    config.cache_updated_at = timezone.now()
                else:
                    # Don't mark cache as fresh when no IT sheet data found —
                    # next page load must retry rather than serve stale empty result.
                    config.cache_updated_at = None
                    available = result.get('available_sheets', [])
                    if available:
                        error = (
                            f'SharePoint file loaded but the "IT" worksheet was not found. '
                            f'Available sheets: {", ".join(available)}. '
                            f'Rename the sheet to "IT" and click Refresh.'
                        )
                    else:
                        error = (
                            'SharePoint file loaded but no worksheet data was returned. '
                            'Check that the file is a valid Excel workbook with an "IT" sheet.'
                        )
                config.save(update_fields=['web_url', 'embed_url', 'cached_sheets', 'cache_updated_at'])
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status == 403:
                    error = "You don't have permission to access this file in SharePoint."
                else:
                    logger.exception('Budget SharePoint fetch failed')
                    error = str(exc)

    # ── Load cached sheets ────────────────────────────────────────────────────
    sheets = []
    dashboard = None
    if config.cached_sheets:
        try:
            sheets = json.loads(config.cached_sheets)
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
        'error': error,
        'needs_relogin': needs_relogin,
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
