from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0008_ticket_solution'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name='ticket',
            name='priority',
        ),
        migrations.DeleteModel(
            name='SLAPolicy',
        ),
    ]
