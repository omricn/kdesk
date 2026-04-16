from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0004_change_reminder_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='reminded_done_followup',
            field=models.BooleanField(default=False),
        ),
    ]
