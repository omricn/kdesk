import json
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1)
def parse_budget_file(self, budget_file_id):
    from .models import BudgetFile
    from .views import excel_to_sheets_html

    try:
        bf = BudgetFile.objects.get(pk=budget_file_id)
    except BudgetFile.DoesNotExist:
        return

    try:
        with bf.file.open('rb') as f:
            sheets = excel_to_sheets_html(f)
        bf.rendered_sheets = json.dumps(sheets)
        bf.is_processing = False
        bf.save(update_fields=['rendered_sheets', 'is_processing'])
    except Exception as exc:
        logger.exception('Budget file parse error for id=%s', budget_file_id)
        bf.is_processing = False
        bf.rendered_sheets = ''
        bf.save(update_fields=['rendered_sheets', 'is_processing'])
        raise
