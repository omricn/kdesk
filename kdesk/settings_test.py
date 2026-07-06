"""Kdesk — TEST settings. Standalone, no external services, for `manage.py test`.

Inherits production settings, then forces SQLite, in-process Celery, blank
external credentials, local storage, and in-memory email so the suite runs on a
workstation with no Azure/Redis/Graph access. Keeps the normal URLconf.

Run:  python manage.py test --settings=kdesk.settings_test
"""
from pathlib import Path
from .settings import *  # noqa: F401,F403

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = False
SECRET_KEY = 'test-insecure-key-000000000000000000000000'  # noqa: S105
ALLOWED_HOSTS = ['*']

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
MEDIA_ROOT = BASE_DIR / 'media_test'

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'

# Inert external credentials so integrations import but never reach the network.
AZURE_STORAGE_ACCOUNT = ''
AZURE_TENANT_ID = AZURE_CLIENT_ID = AZURE_CLIENT_SECRET = ''
GROQ_API_KEY = ''
ANTHROPIC_API_KEY = ''
HIBOB_SYNC_API_KEY = 'test-key'
SERVICEDESK_EMAIL = 'servicedesk@test.local'
SITE_URL = 'http://testserver'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Fast hashing in tests.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
