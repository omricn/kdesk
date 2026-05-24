from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0003_provisioningsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='provisioningrequest',
            name='force_create',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='blocked_by_email',
            field=models.CharField(blank=True, max_length=200),
        ),
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
                ],
                default='pending',
                max_length=20,
            ),
        ),
    ]
