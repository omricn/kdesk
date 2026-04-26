from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budget', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='budgetfile',
            name='is_processing',
            field=models.BooleanField(default=False),
        ),
    ]
