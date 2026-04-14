"""
Run this once after first deployment to seed SLA policies and Celery Beat tasks.
Usage: python manage.py setup_kdesk
"""
from django.core.management.base import BaseCommand
from tickets.models import SLAPolicy


class Command(BaseCommand):
    help = 'Seeds default SLA policies and registers scheduled tasks'

    def handle(self, *args, **options):
        defaults = [
            ('low', 8, 72),
            ('medium', 4, 24),
            ('high', 2, 8),
            ('critical', 1, 4),
        ]
        for priority, response_h, resolution_h in defaults:
            _, created = SLAPolicy.objects.get_or_create(
                priority=priority,
                defaults={
                    'response_time_hours': response_h,
                    'resolution_time_hours': resolution_h,
                }
            )
            status = 'created' if created else 'already exists'
            self.stdout.write(f'  SLA [{priority}]: {status}')

        from tasks.scheduled import register_periodic_tasks
        register_periodic_tasks()
        self.stdout.write(self.style.SUCCESS('Scheduled tasks registered.'))

        self.stdout.write(self.style.SUCCESS('Kdesk setup complete!'))
