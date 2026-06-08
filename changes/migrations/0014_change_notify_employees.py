from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0013_backfill_reminded_upcoming'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='notify_employees',
            field=models.BooleanField(default=True),
        ),
    ]
