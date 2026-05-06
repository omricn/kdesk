from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_user_ticket_list_filter'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='notification_sound',
            field=models.CharField(
                choices=[
                    ('silent', 'Silent'),
                    ('ding', 'Ding'),
                    ('chime', 'Chime'),
                    ('double', 'Double beep'),
                    ('tritone', 'Tri-tone'),
                ],
                default='ding',
                max_length=20,
            ),
        ),
    ]
