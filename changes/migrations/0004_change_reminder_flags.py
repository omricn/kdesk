from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0003_change_planned_date_split'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='reminded_start',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='change',
            name='reminded_done',
            field=models.BooleanField(default=False),
        ),
    ]
