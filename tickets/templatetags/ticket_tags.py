from django import template

register = template.Library()


@register.filter
def status_badge(status):
    classes = {
        'new':            'bg-primary',
        'in_progress':    'bg-info',
        'pending_user':   'bg-warning',
        'pending_vendor': 'bg-warning',
        'hold':           'bg-secondary',
        'closed':         'bg-secondary',
    }
    return classes.get(status, 'bg-secondary')


@register.filter
def sla_row_class(ticket):
    status = ticket.sla_status
    if status == 'breached':
        return 'table-danger'
    if status == 'warning':
        return 'table-warning'
    return ''
