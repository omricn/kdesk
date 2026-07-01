from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0032_sentbroadcast'),
    ]

    operations = [
        migrations.AddField(
            model_name='sentbroadcast',
            name='body_html',
            field=models.TextField(blank=True),
        ),
    ]
