from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0005_provisioningrequest_paused_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='provisioningrequest',
            name='create_priority_ticket',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='priority_permissions_as',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='create_salesforce_ticket',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='salesforce_country_permission',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
