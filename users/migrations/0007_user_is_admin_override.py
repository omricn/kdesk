from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_user_notification_sound'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_admin_override',
            field=models.BooleanField(default=False, help_text='Pin as admin — sync will never demote this user.'),
        ),
    ]
