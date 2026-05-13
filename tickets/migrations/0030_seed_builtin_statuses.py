from django.db import migrations

BUILTIN_STATUSES = [
    {'key': 'new',              'label': 'New',                       'badge_class': 'bg-primary',         'is_terminal': False, 'pauses_sla': False, 'order': 10},
    {'key': 'in_progress',      'label': 'In Progress',               'badge_class': 'bg-info',             'is_terminal': False, 'pauses_sla': False, 'order': 20},
    {'key': 'pending_user',     'label': 'Pending User Reply',        'badge_class': 'bg-warning',          'is_terminal': False, 'pauses_sla': True,  'order': 30},
    {'key': 'pending_vendor',   'label': 'Pending Vendor',            'badge_class': 'bg-warning',          'is_terminal': False, 'pauses_sla': True,  'order': 40},
    {'key': 'hold',             'label': 'Hold',                      'badge_class': 'bg-secondary',        'is_terminal': False, 'pauses_sla': True,  'order': 50},
    {'key': 'pending_manager',  'label': 'Pending Manager Approval',  'badge_class': 'bg-pending-manager',  'is_terminal': False, 'pauses_sla': True,  'order': 60},
    {'key': 'user_responded',   'label': 'User Responded',            'badge_class': 'bg-user-responded',   'is_terminal': False, 'pauses_sla': False, 'order': 70},
    {'key': 'requires_spec',    'label': 'Requires Specification',    'badge_class': 'bg-secondary',        'is_terminal': False, 'pauses_sla': True,  'order': 80},
    {'key': 'developer',        'label': 'Developer',                 'badge_class': 'bg-secondary',        'is_terminal': False, 'pauses_sla': True,  'order': 90},
    {'key': 'closed',           'label': 'Closed',                    'badge_class': 'bg-secondary',        'is_terminal': True,  'pauses_sla': False, 'order': 100},
]


def seed_statuses(apps, schema_editor):
    TicketStatus = apps.get_model('tickets', 'TicketStatus')
    for s in BUILTIN_STATUSES:
        TicketStatus.objects.update_or_create(
            key=s['key'],
            defaults={**s, 'is_builtin': True, 'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0029_ticketstatus'),
    ]

    operations = [
        migrations.RunPython(seed_statuses, migrations.RunPython.noop),
    ]
