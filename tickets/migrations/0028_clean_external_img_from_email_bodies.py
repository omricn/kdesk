"""
Data migration: strip external http/https img tags from stored HTML TicketEmail bodies.
These caused browser page freezes by fetching external resources on ticket detail load.
"""
import re
from django.db import migrations

_EXT_IMG_RE = re.compile(r'<img[^>]+src=["\']https?://[^"\']*["\'][^>]*/?>', re.IGNORECASE)


def clean_bodies(apps, schema_editor):
    TicketEmail = apps.get_model('tickets', 'TicketEmail')
    for email in TicketEmail.objects.filter(body_is_html=True):
        cleaned = _EXT_IMG_RE.sub('', email.body)
        if cleaned != email.body:
            email.body = cleaned
            email.save(update_fields=['body'])


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0027_ticketemail_body_is_html'),
    ]

    operations = [
        migrations.RunPython(clean_bodies, migrations.RunPython.noop),
    ]
