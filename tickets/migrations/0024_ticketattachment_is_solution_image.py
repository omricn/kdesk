from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0023_ticket_email_conversation_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketattachment',
            name='is_solution_image',
            field=models.BooleanField(default=False),
        ),
    ]
