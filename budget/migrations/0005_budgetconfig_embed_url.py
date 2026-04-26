from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budget', '0004_budgetconfig_web_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='budgetconfig',
            name='embed_url',
            field=models.URLField(blank=True, max_length=2000),
        ),
    ]
