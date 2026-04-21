import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()
]

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'crispy_forms',
    'crispy_bootstrap5',
    'django_celery_beat',
    # Local
    'users',
    'tickets',
    'tasks',
    'changes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'kdesk.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'kdesk.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'kdesk'),
        'USER': os.environ.get('DB_USER', 'kdesk'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = 'users.User'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Session ───────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 43200          # 12 hours hard expiry from login

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Jerusalem'
USE_I18N = True
USE_TZ = True

# ── Static & Media ────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# ── Azure Blob Storage for media files (production) ───────────────────────────
# When AZURE_STORAGE_ACCOUNT is set, media uploads go to Azure Blob Storage.
# Static files always use Whitenoise (served from the container).
_azure_storage_account = os.environ.get('AZURE_STORAGE_ACCOUNT', '')
if _azure_storage_account:
    STORAGES['default'] = {'BACKEND': 'storages.backends.azure_storage.AzureStorage'}
    AZURE_ACCOUNT_NAME = _azure_storage_account
    AZURE_ACCOUNT_KEY = os.environ.get('AZURE_STORAGE_KEY', '')
    AZURE_CONTAINER = os.environ.get('AZURE_STORAGE_CONTAINER', 'media')
    AZURE_CUSTOM_DOMAIN = f'{_azure_storage_account}.blob.core.windows.net'
    MEDIA_URL = f'https://{AZURE_CUSTOM_DOMAIN}/{AZURE_CONTAINER}/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Crispy Forms ──────────────────────────────────────────────────────────────
CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

# ── Celery ────────────────────────────────────────────────────────────────────
_redis_url = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
# Azure Cache for Redis uses rediss:// — Celery requires ssl_cert_reqs in the URL
if _redis_url.startswith('rediss://') and 'ssl_cert_reqs' not in _redis_url:
    _redis_url += ('&' if '?' in _redis_url else '?') + 'ssl_cert_reqs=CERT_NONE'
CELERY_BROKER_URL = _redis_url
CELERY_RESULT_BACKEND = _redis_url

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# ── Microsoft Graph ───────────────────────────────────────────────────────────
AZURE_TENANT_ID = os.environ.get('AZURE_TENANT_ID', '')
AZURE_CLIENT_ID = os.environ.get('AZURE_CLIENT_ID', '')
AZURE_CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET', '')
SERVICEDESK_EMAIL = os.environ.get('SERVICEDESK_EMAIL', 'servicedesk@kramerav.com')
ENTRA_USER_GROUP = os.environ.get('ENTRA_USER_GROUP', 'KramerLicensedUsers')
EMAIL_POLL_INTERVAL = int(os.environ.get('EMAIL_POLL_INTERVAL', '5'))

# ── SSO ───────────────────────────────────────────────────────────────────────
# The redirect URI must be registered in the Azure App Registration.
# For local dev: http://localhost:8000/auth/callback/
# For production: http://your-server-ip:8000/auth/callback/
AZURE_REDIRECT_URI = os.environ.get('AZURE_REDIRECT_URI', 'http://localhost:8000/auth/callback/')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:8000')

# Only members of this Entra group (looked up by email) are allowed to log in as admins
ENTRA_ADMIN_GROUP_EMAIL = os.environ.get('ENTRA_ADMIN_GROUP_EMAIL', 'Global_OPS_IT@kramerav.com')
ENTRA_IT_MANAGER_GROUP_EMAIL = os.environ.get('ENTRA_IT_MANAGER_GROUP_EMAIL', 'IT_Manager@kramerav.com')
ENTRA_SUPPORT_ADMIN_GROUP_EMAIL = os.environ.get('ENTRA_SUPPORT_ADMIN_GROUP_EMAIL', 'IT_SupportAdmin@kramerav.com')

# ── Groq AI ──────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[%(levelname)s] %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
