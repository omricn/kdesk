from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tickets', '0031_ticket_requester_email_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='SentBroadcast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(max_length=500)),
                ('header_title', models.CharField(max_length=300)),
                ('sub_line', models.CharField(blank=True, max_length=300)),
                ('body', models.TextField()),
                ('to_recipients', models.TextField(blank=True)),
                ('bcc_recipients', models.TextField(blank=True)),
                ('recipient_count', models.PositiveIntegerField(default=0)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-sent_at'],
            },
        ),
    ]
