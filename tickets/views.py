import csv
import io
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import AttachmentForm, CommentForm, TicketForm, TicketUpdateForm
from .models import (
    SystemSetting, Ticket, TicketAttachment, TicketComment,
    TicketCategory, TicketSubCategory, TicketItem,
)
from users.models import User


def _get_categories_json():
    data = {
        'categories': [],
        'subcategories': [],
        'items': [],
    }
    for cat in TicketCategory.objects.all():
        data['categories'].append({'id': cat.pk, 'name': cat.name})
    for sub in TicketSubCategory.objects.select_related('category').all():
        data['subcategories'].append({'id': sub.pk, 'cat_id': sub.category_id, 'name': sub.name})
    for item in TicketItem.objects.select_related('subcategory').all():
        data['items'].append({'id': item.pk, 'sub_id': item.subcategory_id, 'name': item.name})
    return json.dumps(data)


def _set_default_category(ticket):
    """Set IT > General > New as default category for new tickets."""
    try:
        cat = TicketCategory.objects.get(name='IT')
        sub = TicketSubCategory.objects.get(category=cat, name='General')
        item = TicketItem.objects.get(subcategory=sub, name='New')
        ticket.category = cat
        ticket.subcategory = sub
        ticket.ticket_item = item
    except Exception:
        pass


# ── Dashboard ─────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    tickets = Ticket.objects.select_related('assignee')
    total = tickets.count()
    open_count = tickets.filter(status=Ticket.STATUS_NEW).count()
    in_progress_count = tickets.filter(status=Ticket.STATUS_IN_PROGRESS).count()
    pending_count = tickets.filter(status__in=[
        Ticket.STATUS_PENDING_USER, Ticket.STATUS_PENDING_VENDOR, Ticket.STATUS_HOLD
    ]).count()
    breached_count = tickets.filter(sla_breached=True).exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).count()

    my_tickets = tickets.filter(
        assignee=request.user
    ).exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).order_by('sla_deadline')[:10]

    recent_tickets = tickets.exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).order_by('-created_at')[:10]

    context = {
        'total': total,
        'open_count': open_count,
        'in_progress_count': in_progress_count,
        'pending_count': pending_count,
        'breached_count': breached_count,
        'my_tickets': my_tickets,
        'recent_tickets': recent_tickets,
    }
    return render(request, 'dashboard.html', context)


# ── Ticket List ───────────────────────────────────────────────────────────────

@login_required
def ticket_list(request):
    qs = Ticket.objects.select_related('assignee', 'category', 'subcategory', 'ticket_item').all()

    # Filters from query params
    statuses = request.GET.getlist('status')
    assignee_list = request.GET.getlist('assignee')
    sla_list = request.GET.getlist('sla')
    search = request.GET.get('q', '')
    col_id = request.GET.get('col_id', '').strip()
    col_subject = request.GET.get('col_subject', '').strip()
    col_requester = request.GET.get('col_requester', '').strip()

    if statuses:
        qs = qs.filter(status__in=statuses)
    if assignee_list:
        q = Q()
        if 'me' in assignee_list:
            q |= Q(assignee=request.user)
        if 'unassigned' in assignee_list:
            q |= Q(assignee__isnull=True)
        admin_ids = [a for a in assignee_list if a not in ('me', 'unassigned') and a.isdigit()]
        if admin_ids:
            q |= Q(assignee_id__in=admin_ids)
        qs = qs.filter(q)
    if 'breached' in sla_list:
        qs = qs.filter(sla_breached=True).exclude(status__in=Ticket.TERMINAL_STATUSES)
    if col_id:
        ticket_num = col_id.lstrip('#').lstrip('0') or '0'
        if ticket_num.isdigit():
            qs = qs.filter(pk=int(ticket_num))
    if col_subject:
        qs = qs.filter(title__icontains=col_subject)
    if col_requester:
        qs = qs.filter(
            Q(requester_name__icontains=col_requester) |
            Q(requester_email__icontains=col_requester)
        )
    if search:
        search_q = (
            Q(title__icontains=search) |
            Q(description__icontains=search) |
            Q(requester_email__icontains=search) |
            Q(requester_name__icontains=search)
        )
        ticket_num = search.lstrip('#').lstrip('0') or '0'
        if ticket_num.isdigit():
            search_q |= Q(pk=int(ticket_num))
        qs = qs.filter(search_q)

    admins = User.objects.filter(is_admin=True, is_active=True)

    context = {
        'tickets': qs,
        'admins': admins,
        'status_choices': Ticket.STATUS_CHOICES,
        'current_filters': {
            'status': statuses,
            'assignee': assignee_list,
            'sla': sla_list,
            'q': search,
            'col_id': col_id,
            'col_subject': col_subject,
            'col_requester': col_requester,
        },
        'categories_json': _get_categories_json(),
    }
    return render(request, 'tickets/list.html', context)


# ── Ticket Detail ─────────────────────────────────────────────────────────────

@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related('assignee', 'category', 'subcategory', 'ticket_item'), pk=pk
    )
    comment_form = CommentForm()
    update_form = TicketUpdateForm(instance=ticket)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'comment':
            comment_form = CommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.ticket = ticket
                comment.author = request.user
                comment.save()
                ticket.updated_at = timezone.now()
                ticket.save(update_fields=['updated_at'])
                # Notify assignee of update
                if ticket.assignee and ticket.assignee != request.user and ticket.assignee.notify_on_update:
                    from tasks.scheduled import send_ticket_notification
                    send_ticket_notification.delay('update', ticket.pk, request.user.pk)
                messages.success(request, 'Comment added.')
                return redirect('ticket_detail', pk=pk)

        elif action == 'update':
            update_form = TicketUpdateForm(request.POST, instance=ticket)
            if update_form.is_valid():
                solution = request.POST.get('solution', '').strip()
                closing = update_form.cleaned_data.get('status') in Ticket.TERMINAL_STATUSES
                if closing and not solution:
                    update_form.add_error(None, 'A solution description is required when closing a ticket.')
                else:
                    old_assignee = ticket.assignee
                    was_closed = ticket.status in Ticket.TERMINAL_STATUSES
                    updated = update_form.save(commit=False)
                    updated.solution = solution
                    # Stamp resolved_at when closed
                    if updated.status in Ticket.TERMINAL_STATUSES and not updated.resolved_at:
                        updated.resolved_at = timezone.now()
                    updated.save()
                    # Notify new assignee
                    if updated.assignee and updated.assignee != old_assignee and updated.assignee.notify_on_assign:
                        from tasks.scheduled import send_ticket_notification
                        send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
                    # Notify requester when ticket is newly closed
                    if updated.status in Ticket.TERMINAL_STATUSES and not was_closed:
                        from tasks.scheduled import send_requester_closed
                        send_requester_closed.delay(ticket.pk)
                    messages.success(request, 'Ticket updated.')
                    return redirect('ticket_detail', pk=pk)

        elif action == 'upload':
            file_obj = request.FILES.get('file')
            if file_obj:
                att = TicketAttachment(
                    ticket=ticket,
                    filename=file_obj.name,
                    file=file_obj,
                    file_size=file_obj.size,
                )
                att.save()
                messages.success(request, f'Attachment "{file_obj.name}" uploaded.')
                return redirect('ticket_detail', pk=pk)

    context = {
        'ticket': ticket,
        'comment_form': comment_form,
        'update_form': update_form,
        'categories_json': _get_categories_json(),
    }
    return render(request, 'tickets/detail.html', context)


# ── Create Ticket ─────────────────────────────────────────────────────────────

@login_required
def ticket_create(request):
    form = TicketForm()
    if request.method == 'POST':
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.source = Ticket.SOURCE_MANUAL
            _set_default_category(ticket)
            ticket.save()
            if ticket.assignee and ticket.assignee.notify_on_assign:
                from tasks.scheduled import send_ticket_notification
                send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
            from tasks.scheduled import send_requester_created
            send_requester_created.delay(ticket.pk)
            messages.success(request, f'Ticket #{ticket.pk:04d} created.')
            return redirect('ticket_detail', pk=ticket.pk)

    return render(request, 'tickets/create.html', {'form': form})


@login_required
@require_POST
def ticket_bulk_action(request):
    ticket_ids = request.POST.getlist('ticket_ids')
    action = request.POST.get('action')

    if not ticket_ids:
        messages.warning(request, 'No tickets selected.')
        return redirect('ticket_list')

    qs = Ticket.objects.filter(pk__in=ticket_ids)
    count = qs.count()

    if action == 'delete':
        qs.delete()
        messages.success(request, f'{count} ticket(s) deleted.')

    elif action == 'assign':
        assignee_id = request.POST.get('assignee_id') or None
        if assignee_id:
            try:
                assignee = User.objects.get(pk=assignee_id, is_admin=True, is_active=True)
                qs.update(assignee=assignee)
                messages.success(request, f'{count} ticket(s) assigned to {assignee}.')
            except User.DoesNotExist:
                messages.error(request, 'Selected admin not found.')
        else:
            qs.update(assignee=None)
            messages.success(request, f'{count} ticket(s) unassigned.')

    elif action == 'status':
        new_status = request.POST.get('new_status')
        valid = dict(Ticket.STATUS_CHOICES)
        if new_status in valid:
            qs.update(status=new_status)
            messages.success(request, f'{count} ticket(s) set to {valid[new_status]}.')
        else:
            messages.error(request, 'Invalid status.')

    else:
        messages.error(request, 'Unknown action.')

    return redirect('ticket_list')


@login_required
@require_POST
def ticket_categorize(request, pk):
    """AJAX: set category / subcategory / item on a ticket, with auto-assignment."""
    ticket = get_object_or_404(Ticket.objects.select_related('assignee'), pk=pk)

    def _int_or_none(val):
        try:
            return int(val) if val else None
        except (ValueError, TypeError):
            return None

    cat_id = _int_or_none(request.POST.get('category'))
    sub_id = _int_or_none(request.POST.get('subcategory'))
    item_id = _int_or_none(request.POST.get('ticket_item'))

    ticket.category_id = cat_id
    ticket.subcategory_id = sub_id
    ticket.ticket_item_id = item_id

    # Auto-assign based on subcategory if it has a designated admin
    old_assignee = ticket.assignee
    new_assignee = None
    if sub_id:
        try:
            sub = TicketSubCategory.objects.select_related('assignee').get(pk=sub_id)
            if sub.assignee:
                ticket.assignee = sub.assignee
                new_assignee = sub.assignee
        except TicketSubCategory.DoesNotExist:
            pass

    save_fields = ['category', 'subcategory', 'ticket_item']
    if new_assignee:
        save_fields.append('assignee')
    ticket.save(update_fields=save_fields)

    # Notify new assignee if they changed
    if new_assignee and new_assignee != old_assignee and new_assignee.notify_on_assign:
        try:
            from tasks.scheduled import send_ticket_notification
            send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
        except Exception:
            pass

    parts = [x for x in [
        ticket.category.name if ticket.category else None,
        ticket.subcategory.name if ticket.subcategory else None,
        ticket.ticket_item.name if ticket.ticket_item else None,
    ] if x]
    return JsonResponse({
        'ok': True,
        'label': ' / '.join(parts) if parts else '',
        'assignee': str(ticket.assignee) if ticket.assignee else '',
    })


def _auto_assign():
    """Round-robin assignment: pick the admin with the fewest open tickets."""
    from django.db.models import Count, Q
    admin = (
        User.objects
        .filter(is_admin=True, is_active=True)
        .annotate(open_count=Count(
            'assigned_tickets',
            filter=~Q(assigned_tickets__status__in=Ticket.TERMINAL_STATUSES)
        ))
        .order_by('open_count')
        .first()
    )
    return admin


# ── Reports ───────────────────────────────────────────────────────────────────

@login_required
def reports(request):
    now = timezone.now()
    last_30 = now - timedelta(days=30)

    tickets_30 = Ticket.objects.filter(created_at__gte=last_30)

    by_status = (
        Ticket.objects.values('status')
        .annotate(count=Count('id'))
        .order_by('status')
    )
    by_assignee = (
        Ticket.objects.filter(assignee__isnull=False)
        .values('assignee__display_name', 'assignee__email')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    avg_resolution = None
    resolved = Ticket.objects.filter(
        resolved_at__isnull=False,
        status__in=Ticket.TERMINAL_STATUSES,
        created_at__gte=last_30,
    )
    if resolved.exists():
        total_seconds = sum(
            (t.resolved_at - t.created_at).total_seconds() for t in resolved
        )
        avg_hours = total_seconds / resolved.count() / 3600
        avg_resolution = round(avg_hours, 1)

    context = {
        'tickets_30_count': tickets_30.count(),
        'breached_count': tickets_30.filter(sla_breached=True).count(),
        'resolved_count': resolved.count(),
        'avg_resolution_hours': avg_resolution,
        'by_status': by_status,
        'by_assignee': by_assignee,
    }
    return render(request, 'reports/index.html', context)


@login_required
def export_tickets_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="kdesk_tickets.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'ID', 'Title', 'Status', 'Assignee',
        'Requester Email', 'Requester Name', 'Source',
        'Created At', 'Resolved At', 'SLA Deadline', 'SLA Breached',
    ])

    for t in Ticket.objects.select_related('assignee').all():
        writer.writerow([
            f'#{t.pk:04d}',
            t.title,
            t.get_status_display(),
            str(t.assignee) if t.assignee else '',
            t.requester_email,
            t.requester_name,
            t.get_source_display(),
            t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
            t.resolved_at.strftime('%Y-%m-%d %H:%M') if t.resolved_at else '',
            t.sla_deadline.strftime('%Y-%m-%d %H:%M') if t.sla_deadline else '',
            'Yes' if t.sla_breached else 'No',
        ])

    return response


# ── Settings ──────────────────────────────────────────────────────────────────

@login_required
def settings_view(request):
    if not request.user.is_admin:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'notifications':
            val = '1' if 'notify_requester_on_close' in request.POST else '0'
            SystemSetting.set('notify_requester_on_close', val)
            messages.success(request, 'Notification settings saved.')
            return redirect('settings')

    context = {
        'servicedesk_email': settings.SERVICEDESK_EMAIL,
        'notify_requester_on_close': SystemSetting.get('notify_requester_on_close', '1') == '1',
    }
    return render(request, 'settings.html', context)
