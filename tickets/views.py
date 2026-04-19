import csv
import io
import json
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

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
    TicketCategory, TicketSubCategory, TicketItem, TicketHistory, TicketEmail,
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
    for sub in TicketSubCategory.objects.select_related('category', 'assignee').all():
        data['subcategories'].append({'id': sub.pk, 'cat_id': sub.category_id, 'name': sub.name, 'assignee_id': sub.assignee_id})
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

    from django.utils import timezone
    today = timezone.now().date()

    my_tickets = tickets.filter(
        assignee=request.user
    ).exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).order_by('sla_deadline')[:10]

    recent_qs = tickets.exclude(status__in=Ticket.TERMINAL_STATUSES)
    if not request.user.is_admin:
        recent_qs = recent_qs.filter(assignee=request.user)
    recent_tickets = recent_qs.order_by('-created_at')[:10]

    assigned_to_me_today = tickets.filter(
        assignee=request.user,
        created_at__date=today,
    ).count()

    closed_by_me_today = tickets.filter(
        assignee=request.user,
        status__in=Ticket.TERMINAL_STATUSES,
        updated_at__date=today,
    ).count()

    context = {
        'total': total,
        'open_count': open_count,
        'in_progress_count': in_progress_count,
        'pending_count': pending_count,
        'breached_count': breached_count,
        'my_tickets': my_tickets,
        'recent_tickets': recent_tickets,
        'assigned_to_me_today': assigned_to_me_today,
        'closed_by_me_today': closed_by_me_today,
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

    # Non-admin users default to seeing only their own tickets when no filters applied
    has_any_filter = bool(statuses or assignee_list or sla_list or
                          request.GET.get('q') or request.GET.get('col_id') or
                          request.GET.get('col_subject') or request.GET.get('col_requester'))
    if not request.user.is_admin and not has_any_filter:
        assignee_list = [str(request.user.pk)]
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
            # Capture old values NOW — before is_valid() runs _post_clean()
            # which overwrites the model instance fields with submitted data.
            old_status = ticket.status
            old_assignee = ticket.assignee
            update_form = TicketUpdateForm(request.POST, instance=ticket)
            if update_form.is_valid():
                solution = request.POST.get('solution', '').strip()
                closing = update_form.cleaned_data.get('status') in Ticket.TERMINAL_STATUSES
                if closing and not solution:
                    update_form.add_error(None, 'A solution description is required when closing a ticket.')
                else:
                    was_closed = old_status in Ticket.TERMINAL_STATUSES
                    updated = update_form.save(commit=False)
                    updated.solution = solution
                    # Stamp resolved_at when closed
                    if updated.status in Ticket.TERMINAL_STATUSES and not updated.resolved_at:
                        updated.resolved_at = timezone.now()
                    # Apply category fields before the single save
                    def _to_int(v):
                        try: return int(v) if v else None
                        except (ValueError, TypeError): return None
                    cat_id  = _to_int(request.POST.get('cat_id'))
                    sub_id  = _to_int(request.POST.get('sub_id'))
                    item_id = _to_int(request.POST.get('item_id'))
                    logger.info('[ticket_detail] update pk=%s cat_id=%s sub_id=%s item_id=%s', pk, cat_id, sub_id, item_id)
                    if cat_id is not None or sub_id is not None:
                        updated.category_id    = cat_id
                        updated.subcategory_id = sub_id
                        updated.ticket_item_id = item_id
                    updated.save()
                    # Record history
                    status_labels = dict(Ticket.STATUS_CHOICES)
                    history_entries = []
                    if updated.status != old_status:
                        history_entries.append(TicketHistory(
                            ticket=updated,
                            changed_by=request.user,
                            field='Status',
                            old_value=status_labels.get(old_status, old_status),
                            new_value=status_labels.get(updated.status, updated.status),
                        ))
                    if updated.assignee != old_assignee:
                        history_entries.append(TicketHistory(
                            ticket=updated,
                            changed_by=request.user,
                            field='Assignee',
                            old_value=str(old_assignee) if old_assignee else 'Unassigned',
                            new_value=str(updated.assignee) if updated.assignee else 'Unassigned',
                        ))
                    if history_entries:
                        TicketHistory.objects.bulk_create(history_entries)
                    # Notify new assignee
                    if updated.assignee and updated.assignee != old_assignee and updated.assignee.notify_on_assign:
                        from tasks.scheduled import send_ticket_notification
                        send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
                    # Notify requester when ticket is newly closed
                    if updated.status in Ticket.TERMINAL_STATUSES and not was_closed:
                        from tasks.scheduled import send_requester_closed
                        send_requester_closed.delay(ticket.pk)

                    messages.success(request, 'Ticket updated.')
                    if request.POST.get('next') == 'list':
                        return redirect('ticket_list')
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
        'ticket_history': ticket.history.select_related('changed_by').all(),
    }
    return render(request, 'tickets/detail.html', context)


# ── Create Ticket ─────────────────────────────────────────────────────────────

@login_required
def ticket_create(request):
    form = TicketForm()
    if request.method == 'POST':
        form = TicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.source = Ticket.SOURCE_MANUAL
            _set_default_category(ticket)
            ticket.save()
            uploaded_file = request.FILES.get('attachment')
            if uploaded_file:
                TicketAttachment.objects.create(
                    ticket=ticket,
                    filename=uploaded_file.name,
                    file=uploaded_file,
                    file_size=uploaded_file.size,
                )
            TicketHistory.objects.create(
                ticket=ticket,
                changed_by=request.user,
                field='Ticket created',
                old_value='',
                new_value=f'By {request.user}',
            )
            if ticket.assignee and ticket.assignee.notify_on_assign:
                from tasks.scheduled import send_ticket_notification
                send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
            from tasks.scheduled import send_requester_created, generate_ai_summary
            send_requester_created.delay(ticket.pk)
            generate_ai_summary.delay(ticket.pk)
            messages.success(request, f'Ticket #{ticket.pk:04d} created.')
            return redirect('ticket_detail', pk=ticket.pk)

    return render(request, 'tickets/create.html', {'form': form})


def lookup_user_by_email(request):
    email = request.GET.get('email', '').strip()
    if not email:
        return JsonResponse({'name': ''})
    user = User.objects.filter(email__iexact=email).first()
    return JsonResponse({'name': user.display_name or '' if user else ''})


def user_search(request):
    """Return users matching a partial email or display name (for autocomplete)."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'users': []})
    from django.db.models import Q
    users = (
        User.objects
        .filter(is_active=True)
        .filter(Q(email__icontains=q) | Q(display_name__icontains=q))
        .order_by('display_name')[:10]
    )
    return JsonResponse({'users': [
        {'email': u.email, 'name': u.display_name or u.email}
        for u in users
    ]})


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

    # Capture old category label before overwriting
    old_cat_parts = [x for x in [
        ticket.category.name if ticket.category_id else None,
        ticket.subcategory.name if ticket.subcategory_id else None,
        ticket.ticket_item.name if ticket.ticket_item_id else None,
    ] if x]
    old_cat_str = ' / '.join(old_cat_parts)

    ticket.category_id = cat_id
    ticket.subcategory_id = sub_id
    ticket.ticket_item_id = item_id

    # Auto-assign based on subcategory assignee
    old_assignee = ticket.assignee
    if sub_id:
        from .models import TicketSubCategory
        try:
            sub = TicketSubCategory.objects.select_related('assignee').get(pk=sub_id)
            if sub.assignee_id:
                ticket.assignee = sub.assignee
        except TicketSubCategory.DoesNotExist:
            pass

    update_fields = ['category', 'subcategory', 'ticket_item']
    if ticket.assignee != old_assignee:
        update_fields.append('assignee')

    ticket.save(update_fields=update_fields)

    parts = [x for x in [
        ticket.category.name if ticket.category else None,
        ticket.subcategory.name if ticket.subcategory else None,
        ticket.ticket_item.name if ticket.ticket_item else None,
    ] if x]
    new_cat_str = ' / '.join(parts) if parts else ''
    if new_cat_str != old_cat_str:
        TicketHistory.objects.create(
            ticket=ticket,
            changed_by=request.user,
            field='Category',
            old_value=old_cat_str,
            new_value=new_cat_str,
        )
    if ticket.assignee != old_assignee:
        TicketHistory.objects.create(
            ticket=ticket,
            changed_by=request.user,
            field='Assignee',
            old_value=str(old_assignee) if old_assignee else '',
            new_value=str(ticket.assignee) if ticket.assignee else '',
        )
    return JsonResponse({
        'ok': True,
        'label': new_cat_str,
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
    if not request.user.is_superuser:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'notifications':
            val = '1' if 'notify_requester_on_close' in request.POST else '0'
            SystemSetting.set('notify_requester_on_close', val)
            messages.success(request, 'Notification settings saved.')
            return redirect('settings')

        elif action == 'sla_suspend':
            reason = request.POST.get('sla_pause_reason', '').strip()
            SystemSetting.set('sla_paused', '1')
            SystemSetting.set('sla_pause_started_at', timezone.now().isoformat())
            SystemSetting.set('sla_pause_reason', reason)
            messages.warning(request, 'SLA suspended. Ticket clocks are frozen.')
            return redirect('settings')

        elif action == 'sla_resume':
            from tickets.sla import business_hours_elapsed, add_business_hours, SLA_HOURS
            from django.utils.dateparse import parse_datetime

            pause_started_at = parse_datetime(SystemSetting.get('sla_pause_started_at', ''))
            resume_time = timezone.now()

            if pause_started_at:
                open_tickets = Ticket.objects.filter(
                    sla_deadline__isnull=False
                ).exclude(status__in=Ticket.TERMINAL_STATUSES)
                for ticket in open_tickets:
                    elapsed_at_pause = business_hours_elapsed(ticket.created_at, pause_started_at)
                    remaining = max(0.0, SLA_HOURS - elapsed_at_pause)
                    ticket.sla_deadline = add_business_hours(resume_time, remaining)
                    ticket.save(update_fields=['sla_deadline'])

            SystemSetting.set('sla_paused', '0')
            SystemSetting.set('sla_pause_started_at', '')
            SystemSetting.set('sla_pause_reason', '')
            messages.success(request, 'SLA resumed. Ticket deadlines have been recalculated.')
            return redirect('settings')

    sla_paused = SystemSetting.get('sla_paused', '0') == '1'
    sla_pause_reason = SystemSetting.get('sla_pause_reason', '')
    sla_pause_started_raw = SystemSetting.get('sla_pause_started_at', '')
    sla_pause_started = None
    if sla_pause_started_raw:
        from django.utils.dateparse import parse_datetime
        sla_pause_started = parse_datetime(sla_pause_started_raw)

    context = {
        'servicedesk_email': settings.SERVICEDESK_EMAIL,
        'notify_requester_on_close': SystemSetting.get('notify_requester_on_close', '1') == '1',
        'sla_paused': sla_paused,
        'sla_pause_reason': sla_pause_reason,
        'sla_pause_started': sla_pause_started,
    }
    return render(request, 'settings.html', context)


# ── Ticket email correspondence ───────────────────────────────────────────────

@login_required
@require_POST
def ticket_send_email(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    body = request.POST.get('email_body', '').strip()
    if not body:
        messages.error(request, 'Email body cannot be empty.')
        return redirect('ticket_detail', pk=pk)

    subject = f'[Ticket #{ticket.pk:04d}] {ticket.title}'
    to_email = ticket.requester_email
    sender_name = request.user.display_name or request.user.email

    html_body = f"""
    <p>{body.replace(chr(10), '<br>')}</p>
    <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
    <p style="color:#888;font-size:12px;">
      {sender_name} · IT Support Team<br>
      Ticket reference: <strong>#{ticket.pk:04d}</strong>
    </p>
    """

    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=to_email,
            subject=subject,
            body_html=html_body,
        )
    except Exception as exc:
        messages.error(request, f'Failed to send email: {exc}')
        return redirect('ticket_detail', pk=pk)

    TicketEmail.objects.create(
        ticket=ticket,
        direction=TicketEmail.DIRECTION_SENT,
        subject=subject,
        body=body,
        from_email=settings.SERVICEDESK_EMAIL,
        to_email=to_email,
        sent_by=request.user,
    )

    messages.success(request, f'Email sent to {to_email}.')
    return redirect('ticket_detail', pk=pk)


# ── Email preview (superuser only) ───────────────────────────────────────────

@login_required
def email_preview(request):
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from tasks.scheduled import _email_html, _row

    site = settings.SITE_URL

    samples = [
        {
            'label': 'Ticket Assigned',
            'html': _email_html(
                header_title='Ticket Assigned to You',
                header_subtitle='#0042 — Outlook not syncing emails',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>A support ticket has been assigned to you.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails') +
                    _row('Requester', 'David Levi (dlevi@kramerav.com)') +
                    _row('SLA Deadline', '18 Apr 2026 14:00')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='Open Ticket',
            ),
        },
        {
            'label': 'Ticket Updated',
            'html': _email_html(
                header_title='Ticket Updated',
                header_subtitle='#0042 — Outlook not syncing emails',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>Ticket <strong>#0042</strong> was updated by <strong>Shahar Dekner</strong>.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails') +
                    _row('Status', 'In Progress') +
                    _row('Updated by', 'Shahar Dekner')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='Open Ticket',
            ),
        },
        {
            'label': 'SLA Warning',
            'html': _email_html(
                header_title='SLA Warning',
                header_subtitle='#0042 — Outlook not syncing emails',
                header_color='#e67e22',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>This ticket is at <strong>78% of its SLA window</strong>. Please respond soon to avoid a breach.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails', '#e67e22') +
                    _row('SLA Deadline', '18 Apr 2026 14:00', '#e67e22') +
                    _row('Elapsed', '78%', '#e67e22')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='Open Ticket',
            ),
        },
        {
            'label': 'SLA Breached',
            'html': _email_html(
                header_title='SLA Deadline Breached',
                header_subtitle='#0042 — Outlook not syncing emails',
                header_color='#c0392b',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>The following ticket has <strong>breached its SLA deadline</strong> and requires your immediate attention.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails', '#c0392b') +
                    _row('Requester', 'David Levi (dlevi@kramerav.com)', '#c0392b') +
                    _row('SLA Deadline', '17 Apr 2026 14:00', '#c0392b')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='Open Ticket',
            ),
        },
        {
            'label': 'Requester — Ticket Received',
            'html': _email_html(
                header_title='We received your request',
                header_subtitle='Ticket #0042',
                greeting='Hi <strong>David Levi</strong>,<br><br>Your support request has been received and logged. Our IT team will look into it and get back to you as soon as possible.',
                body_rows=(
                    _row('Ticket #', '#0042') +
                    _row('Subject', 'Outlook not syncing emails') +
                    _row('Submitted', '16 Apr 2026 09:31')
                ),
            ),
        },
        {
            'label': 'Requester — Ticket Closed',
            'html': _email_html(
                header_title='Your ticket has been closed',
                header_subtitle='Ticket #0042',
                greeting='Hi <strong>David Levi</strong>,<br><br>Your support ticket has been resolved and closed. If you need further assistance, please don\'t hesitate to reach out.',
                body_rows=(
                    _row('Ticket #', '#0042') +
                    _row('Subject', 'Outlook not syncing emails') +
                    _row('Closed', '17 Apr 2026 11:20') +
                    _row('Resolution', 'Reconfigured Exchange profile and cleared local cache. Emails are now syncing correctly.')
                ),
            ),
        },
        {
            'label': 'Change — Pending Approval (to IT Manager)',
            'html': _email_html(
                header_title='Change Request Pending Approval',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                greeting='A new change request has been submitted and is awaiting your approval.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('Risk Level', 'High') +
                    _row('Affected System', 'Network') +
                    _row('Planned Date', '20 Apr 2026  22:00 – 23:00') +
                    _row('Submitted By', 'Omri Cohen')
                ),
                cta_url=f'{site}/changes/7/',
                cta_label='Review &amp; Approve in Kdesk',
            ),
        },
        {
            'label': 'Change — Submitted (to submitter)',
            'html': _email_html(
                header_title='Change Request Submitted',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>Your change request has been submitted and is now pending approval by the IT Manager. You will be notified once it is approved.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('Planned Date', '20 Apr 2026  22:00 – 23:00')
                ),
                cta_url=f'{site}/changes/7/',
                cta_label='View in Kdesk',
            ),
        },
        {
            'label': 'Change — Approved',
            'html': _email_html(
                header_title='Change Request Approved',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                header_color='#1a7a4a',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>Your change request has been <strong>approved</strong>. You may now proceed with implementation.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment', '#1a7a4a') +
                    _row('Planned Date', '20 Apr 2026  22:00 – 23:00', '#1a7a4a')
                ),
                cta_url=f'{site}/changes/7/',
                cta_label='View in Kdesk',
            ),
        },
        {
            'label': 'Change — Completed',
            'html': _email_html(
                header_title='Change Marked as Done',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>Change <strong>#0007 — Firewall rule update — DMZ segment</strong> has been marked as <strong>Done</strong>. Well done!',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('Affected System', 'Network') +
                    _row('Risk Level', 'High') +
                    _row('Implemented By', 'Omri Cohen')
                ),
            ),
        },
        {
            'label': 'Change — Reminder: Mark as In Progress',
            'html': _email_html(
                header_title='Action Needed — Mark as In Progress',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>The planned maintenance window for <strong>Network</strong> has started (22:00 – 23:00). Please mark the change as <strong>In Progress</strong> in Kdesk so the team knows the work has begun.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('System', 'Network') +
                    _row('Date', 'Sunday, 20 April 2026') +
                    _row('Timeframe', '22:00 – 23:00')
                ),
                cta_url=f'{site}/changes/7/',
                cta_label='Mark as In Progress in Kdesk',
            ),
        },
        {
            'label': 'Change — Reminder: Mark as Done',
            'html': _email_html(
                header_title='Action Needed — Mark as Done',
                header_subtitle='#0007 — Firewall rule update — DMZ segment',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>The planned maintenance window for <strong>Network</strong> has ended (22:00 – 23:00). Please mark the change as <strong>Done</strong> in Kdesk once the work is complete.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('System', 'Network') +
                    _row('Date', 'Sunday, 20 April 2026') +
                    _row('Timeframe', '22:00 – 23:00')
                ),
                cta_url=f'{site}/changes/7/',
                cta_label='Mark as Done in Kdesk',
            ),
        },
        {
            'label': 'Maintenance Broadcast (to all employees)',
            'html': _email_html(
                header_title='Planned Maintenance Notification',
                header_subtitle='Network — Sunday, 20 April 2026',
                greeting=(
                    'Dear Employees,<br><br>'
                    'Please be informed that the IT Department has scheduled a <strong>Planned Maintenance</strong> '
                    'window. During this time, the affected system may be temporarily unavailable.<br><br>'
                    'We apologize for any inconvenience and will work to minimize disruption. '
                    'If you have any questions please contact '
                    '<a href="mailto:servicedesk@kramerav.com" style="color:#8200B4;">servicedesk@kramerav.com</a>.'
                ),
                body_rows=(
                    _row('System', 'Network') +
                    _row('Date', 'Sunday, 20 April 2026') +
                    _row('Timeframe', '22:00 – 23:00') +
                    _row('Region', 'Israel')
                ),
            ),
        },
    ]

    idx = request.GET.get('idx')
    if idx is not None:
        try:
            from django.http import HttpResponse
            from django.views.decorators.clickjacking import xframe_options_exempt
            response = HttpResponse(samples[int(idx)]['html'])
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
        except (IndexError, ValueError):
            from django.http import Http404
            raise Http404

    return render(request, 'tickets/email_preview.html', {
        'samples': [(i, s['label']) for i, s in enumerate(samples)],
    })
