from django.db import migrations

CATEGORY_DATA = {
    'HR': {
        'Change Required': ['Change Required'],
        'New Employee': ['New Employee'],
        'Terminate Employee': ['Terminate Employee'],
    },
    'IT': {
        'Agile': ['Agile'],
        'BI': ['Power BI'],
        'General': ['New'],
        'Infra HW': [
            'Disk On Key', 'Headphones', 'Kbar', 'Keyboard', 'Laptop',
            'Meeting Rooms', 'Monitor', 'Mouse', 'Network Card', 'Other',
            'PC', 'Printer', 'VIA', 'WebCam',
        ],
        'Infra NET': [
            'Internet Access', 'Other', 'Permissions', 'Phishing',
            'Remote Access - SSL VPN', 'Restore', 'User locked',
        ],
        'Infra SW': [
            'Adobe', 'AI', 'AntiVirus', 'BarTender', 'CAM', 'Corel Draw',
            'HiBOB', 'Internet Browser', 'Monday.com', 'MS Office', 'MS Teams',
            'OneDrive', 'Orcad', 'Other', 'Outlook', 'Solidedge', 'Solidworks',
            'TeamViewer', 'WeSign', 'Windows',
        ],
        'Kramer-Website': ['Other', 'Performance'],
        'Priority': ['Access and Permissions', 'Error'],
        'QV': ['Malfunction', 'Other', 'Permissions'],
        'Salesforce': ['Customization', 'Malfunction', 'New User'],
    },
}


def load_categories(apps, schema_editor):
    TicketCategory = apps.get_model('tickets', 'TicketCategory')
    TicketSubCategory = apps.get_model('tickets', 'TicketSubCategory')
    TicketItem = apps.get_model('tickets', 'TicketItem')

    for cat_name, subcats in CATEGORY_DATA.items():
        cat = TicketCategory.objects.create(name=cat_name)
        for sub_name, items in subcats.items():
            sub = TicketSubCategory.objects.create(category=cat, name=sub_name)
            for item_name in items:
                TicketItem.objects.create(subcategory=sub, name=item_name)


def unload_categories(apps, schema_editor):
    TicketCategory = apps.get_model('tickets', 'TicketCategory')
    TicketCategory.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0002_categories'),
    ]

    operations = [
        migrations.RunPython(load_categories, unload_categories),
    ]
