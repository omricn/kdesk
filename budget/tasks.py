import json
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, soft_time_limit=600, time_limit=660)
def refresh_budget_cache(self, config_pk):
    """Download and parse the SharePoint Excel budget file in the background.
    Uses the app service account token so it can run without a user session.
    """
    from .models import BudgetConfig
    from .graph import fetch_sheets_html

    try:
        config = BudgetConfig.objects.get(pk=config_pk)
        if not config.sharepoint_url:
            return

        result = fetch_sheets_html(config.sharepoint_url, token=None)
        sheets = result['sheets']

        if result.get('web_url'):
            config.web_url = result['web_url']
        if result.get('embed_url'):
            config.embed_url = result['embed_url']

        if sheets:
            config.cached_sheets = json.dumps(sheets)
            config.cache_updated_at = timezone.now()
            logger.info('Budget cache refreshed with %d sheets', len(sheets))
        else:
            available = result.get('available_sheets', [])
            logger.warning('Budget refresh: IT sheet not found. Available: %s', available)
            # Store '[]' so the view knows "fetched but no IT sheet" vs '' which means "still loading"
            config.cached_sheets = json.dumps({'_error': (
                f'SharePoint file loaded but the "IT" worksheet was not found. '
                f'Available sheets: {", ".join(available)}. '
                f'Rename the sheet to "IT" and click Refresh.'
                if available else
                'SharePoint file loaded but no worksheet data was returned.'
            )})
            config.cache_updated_at = timezone.now()

        config.save(update_fields=['web_url', 'embed_url', 'cached_sheets', 'cache_updated_at'])

    except Exception as exc:
        logger.exception('Budget cache refresh failed: %s', exc)
        # Mark as "fetch attempted but failed" so the view doesn't loop
        try:
            config.cached_sheets = json.dumps({'_error': str(exc)})
            config.cache_updated_at = timezone.now()
            config.save(update_fields=['cached_sheets', 'cache_updated_at'])
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=120)
