from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_user_is_it_manager'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='department',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
