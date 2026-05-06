from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_user_department'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ticket_list_filter',
            field=models.TextField(blank=True, default=''),
        ),
    ]
