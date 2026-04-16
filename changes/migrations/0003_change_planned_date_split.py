from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0002_change_affected_region'),
    ]

    operations = [
        migrations.AlterField(
            model_name='change',
            name='planned_date',
            field=models.DateField(),
        ),
        migrations.AddField(
            model_name='change',
            name='planned_from',
            field=models.TimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='change',
            name='planned_to',
            field=models.TimeField(null=True, blank=True),
        ),
    ]
