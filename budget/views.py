import json
import logging

from django.contrib import messages
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
    if config.sharepoint_url and not config.cache_is_fresh():
        try:
            from .graph import fetch_sheets_html
            sheets = fetch_sheets_html(config.sharepoint_url)
            config.cached_sheets = json.dumps(sheets)
            config.cache_updated_at = timezone.now()
            config.save(update_fields=['cached_sheets', 'cache_updated_at'])
        except Exception as exc:
            logger.exception('Budget SharePoint fetch failed')
            error = str(exc)

    # ── Load cached sheets ────────────────────────────────────────────────────
    sheets = []
    if config.cached_sheets:
        try:
            sheets = json.loads(config.cached_sheets)
        except Exception:
            pass

    return render(request, 'budget/budget.html', {
        'config': config,
        'sheets': sheets,
        'error': error,
    })
