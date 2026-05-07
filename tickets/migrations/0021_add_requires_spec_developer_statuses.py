from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0020_ticketcomment_updated_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'New'),
                    ('in_progress', 'In Progress'),
                    ('pending_user', 'Pending User Reply'),
                    ('pending_vendor', 'Pending Vendor'),
                    ('hold', 'Hold'),
                    ('pending_manager', 'Pending Manager Approval'),
                    ('closed', 'Closed'),
                    ('user_responded', 'User Responded'),
                    ('requires_spec', 'Requires Specification'),
                    ('developer', 'Developer'),
                ],
                db_index=True,
                default='new',
                max_length=20,
            ),
        ),
    ]
