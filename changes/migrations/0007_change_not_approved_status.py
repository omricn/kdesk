from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0006_alter_change_affected_system_alter_change_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='change',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'New'),
                    ('pending_approval', 'Pending Approval'),
                    ('approved', 'Approved'),
                    ('not_approved', 'Not Approved'),
                    ('in_progress', 'In Progress'),
                    ('done', 'Done'),
                    ('cancelled', 'Cancelled'),
                ],
                db_index=True,
                default='new',
                max_length=20,
            ),
        ),
    ]
