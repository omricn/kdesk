from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budget', '0002_budgetfile_is_processing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BudgetConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sharepoint_url', models.URLField(blank=True, max_length=1000)),
                ('cached_sheets', models.TextField(blank=True)),
                ('cache_updated_at', models.DateTimeField(blank=True, null=True)),
                ('configured_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='budget_configs', to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
