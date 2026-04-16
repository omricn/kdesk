from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='affected_region',
            field=models.CharField(
                choices=[('israel', 'Israel'), ('global', 'Globally')],
                default='israel',
                max_length=20,
            ),
        ),
    ]
