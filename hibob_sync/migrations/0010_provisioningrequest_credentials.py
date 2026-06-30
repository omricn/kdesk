from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0009_provisioningrequest_salesforce_permissions_as'),
    ]

    operations = [
        migrations.AddField(
            model_name='provisioningrequest',
            name='temp_password',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='manager_email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='provisioningrequest',
            name='credentials_viewed',
            field=models.BooleanField(default=False),
        ),
    ]
