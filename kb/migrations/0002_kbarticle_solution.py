from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kb', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='kbarticle',
            name='solution',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='kbarticle',
            name='body',
            field=models.TextField(blank=True, default=''),
        ),
    ]
