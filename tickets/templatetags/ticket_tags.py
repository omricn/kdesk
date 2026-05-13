from django import template

register = template.Library()


@register.simple_tag
def sla_suspension_info():
    """Returns a dict with SLA suspension state for use in templates."""
    from tickets.models import SystemSetting
    paused = SystemSetting.get('sla_paused', '0') == '1'
    if not paused:
        return None
    reason = SystemSetting.get('sla_pause_reason', '')
    started_str = SystemSetting.get('sla_pause_started_at', '')
    started = None
    if started_str:
        from django.utils.dateparse import parse_datetime
        from django.utils import timezone
        dt = parse_datetime(started_str)
        if dt:
            started = timezone.localtime(dt)
    return {'reason': reason, 'started': started}


@register.filter
def status_badge(status):
    from tickets.models import TicketStatus
    try:
        return TicketStatus.badge_map().get(status, 'bg-secondary')
    except Exception:
        return 'bg-secondary'


@register.filter
def sla_row_class(ticket):
    if getattr(ticket, 'status', None) == 'user_responded':
        return 'row-user-responded'
    return ''


