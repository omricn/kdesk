import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0008_pending_changes_manager_remarks'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChangeAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('change', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='changes.change')),
                ('filename', models.CharField(max_length=255)),
                ('file', models.FileField(upload_to='change_attachments/%Y/%m/')),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
