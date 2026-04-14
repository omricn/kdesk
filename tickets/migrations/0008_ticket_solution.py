from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0007_status_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='solution',
            field=models.TextField(blank=True, help_text='Required when closing a ticket.'),
        ),
    ]
