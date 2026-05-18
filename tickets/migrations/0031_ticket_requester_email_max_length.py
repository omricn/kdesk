from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0030_seed_builtin_statuses'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='requester_email',
            field=models.EmailField(max_length=500, db_index=True),
        ),
    ]
