from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0007_change_not_approved_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='manager_remarks',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='change',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'New'),
                    ('pending_approval', 'Pending Approval'),
                    ('pending_changes', 'Pending Changes'),
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
