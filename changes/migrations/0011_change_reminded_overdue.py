from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0010_change_tickets'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='reminded_overdue',
            field=models.BooleanField(default=False),
        ),
    ]
