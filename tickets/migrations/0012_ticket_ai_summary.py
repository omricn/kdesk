from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0011_email_html_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='ai_summary',
            field=models.TextField(blank=True),
        ),
    ]
