from django.db import migrations

# subcategory name → assignee email
ASSIGNMENTS = {
    'Priority':       'asaban@kramerav.com',
    'BI':             'sdekner@kramerav.com',
    'Salesforce':     'jsuissa@kramerav.com',
    'Kramer-Website': 'sc-aalon@kramerav.com',
}


def set_assignees(apps, schema_editor):
    TicketSubCategory = apps.get_model('tickets', 'TicketSubCategory')
    User = apps.get_model('users', 'User')

    for sub_name, email in ASSIGNMENTS.items():
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            continue
        TicketSubCategory.objects.filter(name=sub_name).update(assignee=user)


def unset_assignees(apps, schema_editor):
    TicketSubCategory = apps.get_model('tickets', 'TicketSubCategory')
    TicketSubCategory.objects.filter(name__in=ASSIGNMENTS.keys()).update(assignee=None)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0004_subcategory_assignee'),
    ]

    operations = [
        migrations.RunPython(set_assignees, unset_assignees),
    ]
