from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncTrigger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_dry_run', models.BooleanField(default=True)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('running', 'Running'),
                             ('completed', 'Completed'), ('failed', 'Failed')],
                    default='pending', max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('triggered_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField()),
                ('completed_at', models.DateTimeField()),
                ('is_dry_run', models.BooleanField()),
                ('matched', models.IntegerField(default=0)),
                ('updated', models.IntegerField(default=0)),
                ('skipped', models.IntegerField(default=0)),
                ('not_found', models.IntegerField(default=0)),
                ('errors', models.IntegerField(default=0)),
                ('raw_log', models.TextField(blank=True)),
                ('success', models.BooleanField(default=True)),
                ('error_message', models.TextField(blank=True)),
                ('log_filename', models.CharField(blank=True, max_length=255)),
                ('trigger', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='run',
                    to='hibob_sync.synctrigger',
                )),
            ],
            options={'ordering': ['-completed_at']},
        ),
        migrations.CreateModel(
            name='SyncChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField()),
                ('field_name', models.CharField(max_length=100)),
                ('old_value', models.TextField(blank=True)),
                ('new_value', models.TextField(blank=True)),
                ('run', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='changes',
                    to='hibob_sync.syncrun',
                )),
            ],
            options={'ordering': ['email', 'field_name']},
        ),
    ]
