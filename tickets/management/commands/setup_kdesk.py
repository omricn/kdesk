"""
Run this once after first deployment to register Celery Beat scheduled tasks.
Usage: python manage.py setup_kdesk
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Registers scheduled Celery Beat tasks for Kdesk'

    def handle(self, *args, **options):
        from tasks.scheduled import register_periodic_tasks
        register_periodic_tasks()
        self.stdout.write(self.style.SUCCESS('Kdesk setup complete — scheduled tasks registered.'))
