from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budget', '0003_budgetconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='budgetconfig',
            name='web_url',
            field=models.URLField(blank=True, max_length=1000),
        ),
    ]
