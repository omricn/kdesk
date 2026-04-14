import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SLAPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('priority', models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')], max_length=20, unique=True)),
                ('response_time_hours', models.PositiveIntegerField(default=24)),
                ('resolution_time_hours', models.PositiveIntegerField(default=72)),
            ],
            options={
                'verbose_name': 'SLA Policy',
                'verbose_name_plural': 'SLA Policies',
                'ordering': ['priority'],
            },
        ),
        migrations.CreateModel(
            name='SystemSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=100, unique=True)),
                ('value', models.TextField(blank=True)),
                ('description', models.CharField(blank=True, max_length=300)),
            ],
        ),
        migrations.CreateModel(
            name='Ticket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=300)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In Progress'), ('pending', 'Pending'), ('resolved', 'Resolved'), ('closed', 'Closed')], db_index=True, default='open', max_length=20)),
                ('priority', models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')], db_index=True, default='medium', max_length=20)),
                ('source', models.CharField(choices=[('email', 'Email'), ('manual', 'Manual')], default='manual', max_length=20)),
                ('requester_email', models.EmailField(db_index=True, max_length=254)),
                ('requester_name', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('sla_deadline', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('sla_breached', models.BooleanField(db_index=True, default=False)),
                ('email_message_id', models.CharField(blank=True, max_length=500, null=True, unique=True)),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_tickets', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TicketComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField()),
                ('is_internal', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='tickets.ticket')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='TicketAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(max_length=255)),
                ('file', models.FileField(upload_to='attachments/%Y/%m/')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='tickets.ticket')),
            ],
        ),
        migrations.CreateModel(
            name='EmailLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message_id', models.CharField(max_length=500, unique=True)),
                ('processed_at', models.DateTimeField(auto_now_add=True)),
                ('error', models.TextField(blank=True)),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='tickets.ticket')),
            ],
        ),
    ]
