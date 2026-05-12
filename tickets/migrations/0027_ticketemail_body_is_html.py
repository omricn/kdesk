from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0026_ticketemail_cc_emails'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketemail',
            name='body_is_html',
            field=models.BooleanField(default=False),
        ),
    ]
