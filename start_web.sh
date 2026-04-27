#!/bin/bash
set -e
python manage.py migrate --noinput
python manage.py collectstatic --noinput
if [ -n "$PROMOTE_SUPERUSER" ]; then
    python manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.filter(email='$PROMOTE_SUPERUSER').first()
if u:
    u.is_staff = True; u.is_superuser = True; u.save()
    print('Promoted:', u.email)
else:
    print('User not found:', '$PROMOTE_SUPERUSER')
"
fi
if [ -n "$DISABLE_POLL_MAILBOX" ]; then
    python manage.py shell -c "
from django_celery_beat.models import PeriodicTask
updated = PeriodicTask.objects.filter(name='Poll Mailbox').update(enabled=False)
print(f'Poll Mailbox disabled (rows={updated})')
"
fi
if [ -n "$ACTIVATE_USER" ]; then
    python manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.filter(email='$ACTIVATE_USER').first()
if u:
    u.is_active = True; u.is_staff = True; u.save()
    print('Activated:', u.email)
else:
    print('User not found: $ACTIVATE_USER')
"
fi
if [ -n "$WIPE_TICKETS" ]; then
    echo "[Startup] Wiping all tickets..."
    python manage.py wipe_tickets
    echo "[Startup] Wipe complete."
fi
if [ -n "$WIPE_CHANGES" ]; then
    echo "[Startup] Wiping all changes..."
    python manage.py wipe_changes
    echo "[Startup] Wipe complete."
fi
echo "[Startup] Syncing users from Entra..."
python manage.py shell -c "
from integrations.user_sync import sync_users, sync_admins
sync_users()
sync_admins()
"
echo "[Startup] User sync complete."
echo "[Startup] Setting subcategory assignees..."
python manage.py fix_subcategory_assignees
echo "[Startup] Done."
exec gunicorn kdesk.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
