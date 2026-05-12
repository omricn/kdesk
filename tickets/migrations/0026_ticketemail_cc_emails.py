from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0025_ticket_merged_into'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketemail',
            name='cc_emails',
            field=models.TextField(blank=True, default=''),
        ),
    ]
