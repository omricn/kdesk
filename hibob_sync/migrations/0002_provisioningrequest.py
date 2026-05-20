import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0001_initial'),
        ('tickets', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProvisioningRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticket', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='provisioning_requests',
                    to='tickets.ticket',
                )),
                ('first_name', models.CharField(max_length=100)),
                ('last_name', models.CharField(max_length=100)),
                ('middle_name', models.CharField(blank=True, max_length=100)),
                ('department', models.CharField(max_length=100)),
                ('division', models.CharField(max_length=100)),
                ('country', models.CharField(max_length=100)),
                ('region', models.CharField(max_length=50)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('personal_mobile', models.CharField(blank=True, max_length=50)),
                ('reports_to', models.CharField(blank=True, max_length=200)),
                ('job_title', models.CharField(blank=True, max_length=200)),
                ('employment_type', models.CharField(blank=True, max_length=100)),
                ('employee_id', models.CharField(blank=True, max_length=50)),
                ('work_email', models.EmailField(blank=True)),
                ('m365_groups', models.JSONField(default=list)),
                ('groups_fallback', models.BooleanField(default=False)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('claimed', 'Claimed'),
                             ('completed', 'Completed'), ('failed', 'Failed')],
                    default='pending', max_length=20,
                )),
                ('is_dry_run', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('result_success', models.BooleanField(blank=True, null=True)),
                ('result_log', models.TextField(blank=True)),
                ('result_message', models.TextField(blank=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
