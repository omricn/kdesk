import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kdesk.settings')

app = Celery('kdesk')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Explicitly import our tasks module since it's named 'scheduled' not 'tasks'
app.autodiscover_tasks(['tasks'], related_name='scheduled')
