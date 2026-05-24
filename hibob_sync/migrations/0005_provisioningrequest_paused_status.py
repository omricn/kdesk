from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0004_provisioningrequest_review_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='provisioningrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('claimed', 'Claimed'),
                    ('completed', 'Completed'),
                    ('failed', 'Failed'),
                    ('review_needed', 'Review Needed'),
                    ('cancelled', 'Cancelled'),
                    ('paused', 'Paused'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
    ]
