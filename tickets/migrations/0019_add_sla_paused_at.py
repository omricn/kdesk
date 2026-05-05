from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0018_add_pending_manager_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='sla_paused_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
