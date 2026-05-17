from django.db import migrations


def backfill_reminded_upcoming(apps, schema_editor):
    """
    Mark reminded_upcoming=True for every change whose 3-hour pre-start
    window has already opened (or passed) at migration time.  This prevents
    the scheduler from sending duplicate broadcasts for changes that were
    either already notified manually or whose window has already begun.
    Future changes (window not yet within 3 hours) are left False so they
    still receive the automatic reminder.
    """
    from datetime import datetime, timedelta
    from django.utils import timezone

    Change = apps.get_model('changes', 'Change')
    now = timezone.now()

    to_mark = []
    for change in Change.objects.filter(reminded_upcoming=False,
                                        planned_date__isnull=False,
                                        planned_from__isnull=False):
        start_dt = datetime.combine(change.planned_date, change.planned_from)
        start_dt_aware = timezone.make_aware(start_dt)
        if now >= start_dt_aware - timedelta(hours=3):
            to_mark.append(change.pk)

    if to_mark:
        Change.objects.filter(pk__in=to_mark).update(reminded_upcoming=True)


class Migration(migrations.Migration):

    dependencies = [
        ('changes', '0012_change_reminded_upcoming'),
    ]

    operations = [
        migrations.RunPython(backfill_reminded_upcoming, migrations.RunPython.noop),
    ]
