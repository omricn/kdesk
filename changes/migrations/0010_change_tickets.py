from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0009_change_attachment'),
        ('tickets', '0020_ticketcomment_updated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='change',
            name='tickets',
            field=models.ManyToManyField(blank=True, related_name='linked_changes', to='tickets.ticket'),
        ),
    ]
