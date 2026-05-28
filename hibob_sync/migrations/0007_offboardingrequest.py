import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hibob_sync', '0006_provisioning_system_tickets'),
        ('tickets', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='OffboardingRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee_email', models.EmailField()),
                ('employee_name', models.CharField(blank=True, max_length=200)),
                ('department', models.CharField(blank=True, max_length=200)),
                ('direct_manager', models.CharField(blank=True, max_length=200)),
                ('country_origin', models.CharField(blank=True, max_length=100)),
                ('termination_date', models.DateField(blank=True, null=True)),
                ('termination_status', models.CharField(blank=True, max_length=100)),
                ('scheduled_for', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('claimed', 'Claimed'),
                        ('completed', 'Completed'),
                        ('failed', 'Failed'),
                        ('review_needed', 'Review Needed'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='pending', max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('result_success', models.BooleanField(blank=True, null=True)),
                ('result_log', models.TextField(blank=True)),
                ('result_message', models.TextField(blank=True)),
                ('ticket', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='offboarding_requests',
                    to='tickets.ticket',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
