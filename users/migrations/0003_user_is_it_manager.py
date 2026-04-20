from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_alter_user_groups_alter_user_is_superuser_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_it_manager',
            field=models.BooleanField(default=False),
        ),
    ]
