from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0014_ticket_requester_department'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='satisfaction_rating',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='satisfaction_text',
            field=models.CharField(blank=True, max_length=25),
        ),
    ]
