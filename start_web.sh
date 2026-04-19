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
python -u manage.py shell -c "
import sys
from integrations.user_sync import sync_users, sync_admins
print('[Startup] Syncing users from Entra...', flush=True)
sync_users()
sync_admins()
print('[Startup] User sync complete.', flush=True)

from tickets.models import TicketSubCategory
from users.models import User
ASSIGNMENTS = {
    'Priority':       'asaban@kramerav.com',
    'BI':             'sdekner@kramerav.com',
    'Salesforce':     'jsuissa@kramerav.com',
    'Kramer-Website': 'sc-aalon@kramerav.com',
    'Infra HW':       'ocohen@kramerav.com',
    'Infra NET':      'ocohen@kramerav.com',
    'Infra SW':       'ocohen@kramerav.com',
}
for sub_name, email in ASSIGNMENTS.items():
    try:
        user = User.objects.get(email=email)
        n = TicketSubCategory.objects.filter(name=sub_name).update(assignee=user)
        print(f'[Startup] subcategory \"{sub_name}\" -> {email} (pk={user.pk}, rows={n})', flush=True)
    except User.DoesNotExist:
        print(f'[Startup] USER NOT FOUND: {email}', flush=True)
    except Exception as e:
        print(f'[Startup] ERROR setting {sub_name}: {e}', flush=True)
print('[Startup] Subcategory assignments done.', flush=True)
"
exec gunicorn kdesk.wsgi:application --bind 0.0.0.0:8000 --workers 3
