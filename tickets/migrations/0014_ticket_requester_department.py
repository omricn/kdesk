from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0013_ticket_email_and_user_responded'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='requester_department',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
