from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0008_offboardingsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='provisioningrequest',
            name='salesforce_permissions_as',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
