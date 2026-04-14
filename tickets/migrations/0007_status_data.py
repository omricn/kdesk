from django.db import migrations

# Map old status values → new status values
STATUS_MAP = {
    'open':     'new',
    'pending':  'pending_user',
    'resolved': 'closed',
    # 'in_progress' and 'closed' are unchanged
}


def migrate_statuses(apps, schema_editor):
    Ticket = apps.get_model('tickets', 'Ticket')
    for old, new in STATUS_MAP.items():
        Ticket.objects.filter(status=old).update(status=new)


def reverse_statuses(apps, schema_editor):
    Ticket = apps.get_model('tickets', 'Ticket')
    for old, new in STATUS_MAP.items():
        Ticket.objects.filter(status=new).update(status=old)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0006_new_statuses'),
    ]

    operations = [
        migrations.RunPython(migrate_statuses, reverse_statuses),
    ]
