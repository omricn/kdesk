from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0022_ticket_email_headers'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='email_conversation_id',
            field=models.CharField(blank=True, db_index=True, max_length=500),
        ),
    ]
