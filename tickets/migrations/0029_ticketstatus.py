from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0028_clean_external_img_from_email_bodies'),
    ]

    operations = [
        migrations.CreateModel(
            name='TicketStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.SlugField(max_length=40, unique=True)),
                ('label', models.CharField(max_length=100)),
                ('badge_class', models.CharField(default='bg-secondary', max_length=50)),
                ('is_terminal', models.BooleanField(default=False, help_text='SLA stops; ticket is done')),
                ('pauses_sla', models.BooleanField(default=False, help_text='SLA clock paused while in this status')),
                ('is_active', models.BooleanField(default=True)),
                ('is_builtin', models.BooleanField(default=False)),
                ('order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'ordering': ['order', 'label'],
            },
        ),
        migrations.AlterField(
            model_name='ticket',
            name='status',
            field=models.CharField(db_index=True, default='new', max_length=40),
        ),
    ]
