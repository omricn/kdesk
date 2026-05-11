from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0024_ticketattachment_is_solution_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='merged_into',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='merged_tickets',
                to='tickets.ticket',
            ),
        ),
    ]
