from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0021_add_requires_spec_developer_statuses'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='email_from',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='ticket',
            name='email_to',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='email_cc',
            field=models.TextField(blank=True),
        ),
    ]
