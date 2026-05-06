from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0019_add_sla_paused_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketcomment',
            name='updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
