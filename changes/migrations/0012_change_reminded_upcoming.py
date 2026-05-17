from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0011_change_reminded_overdue'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='reminded_upcoming',
            field=models.BooleanField(default=False),
        ),
    ]
