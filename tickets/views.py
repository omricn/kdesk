import csv
import io
import json
import logging
from datetime import timedelta
from functools import wraps

logger = logging.getLogger(__name__)

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import CommentForm, PortalTicketForm, TicketForm, TicketUpdateForm


def admin_required(view_func):
    """Requires login + is_admin. Non-admins are redirected to the employee portal."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'{settings.LOGIN_URL}?next={request.path}')
        if not request.user.is_admin:
            return redirect('portal_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def portal_required(view_func):
    """Requires login. Admins are redirected to the admin dashboard unless in portal preview mode."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'{settings.LOGIN_URL}?next={request.path}')
        if request.user.is_admin and not request.session.get('portal_preview'):
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped
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

@admin_required
def dashboard(request):
    from django.utils import timezone
    tickets = Ticket.objects.select_related('assignee')
    my_qs = tickets.filter(assignee=request.user)
    today = timezone.now().date()

    assigned_to_me_today = my_qs.filter(created_at__date=today).count()
    closed_by_me_today = my_qs.filter(
        status__in=Ticket.TERMINAL_STATUSES,
        updated_at__date=today,
    ).count()

    my_tickets = my_qs.exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).order_by('sla_deadline')[:10]

    if request.user.is_superuser:
        stat_qs = tickets
        recent_tickets = (
            tickets.exclude(status__in=Ticket.TERMINAL_STATUSES)
            .order_by('-created_at')[:10]
        )
    else:
        stat_qs = my_qs
        recent_tickets = None

    open_count = stat_qs.filter(status=Ticket.STATUS_NEW).count()
    in_progress_count = stat_qs.filter(status=Ticket.STATUS_IN_PROGRESS).count()
    pending_count = stat_qs.filter(status__in=[
        Ticket.STATUS_PENDING_USER, Ticket.STATUS_PENDING_VENDOR, Ticket.STATUS_HOLD
    ]).count()
    breached_count = stat_qs.filter(sla_breached=True).exclude(
        status__in=Ticket.TERMINAL_STATUSES
    ).count()

    context = {
        'open_count': open_count,
        'in_progress_count': in_progress_count,
        'pending_count': pending_count,
        'breached_count': breached_count,
        'my_tickets': my_tickets,
        'recent_tickets': recent_tickets,
        'assigned_to_me_today': assigned_to_me_today,
        'closed_by_me_today': closed_by_me_today,
        'stats_are_global': request.user.is_superuser,
    }
    return render(request, 'dashboard.html', context)


# ── Ticket List ───────────────────────────────────────────────────────────────

@admin_required
def ticket_list(request):
    from django.db.models import F
    qs = Ticket.objects.select_related('assignee', 'category', 'subcategory', 'ticket_item').all()

    # Filters from query params
    statuses = request.GET.getlist('status')
    assignee_list = request.GET.getlist('assignee')
    sla_list = request.GET.getlist('sla')

    is_explicit_filter = bool(request.GET.get('_f'))
    is_clear = bool(request.GET.get('_clear'))
    has_any_filter = bool(statuses or assignee_list or sla_list or
                          request.GET.get('q') or request.GET.get('col_id') or
                          request.GET.get('col_subject') or request.GET.get('col_requester'))

    if is_clear:
        request.user.ticket_list_filter = ''
        request.user.save(update_fields=['ticket_list_filter'])
        assignee_list = ['me']
    elif is_explicit_filter:
        params = request.GET.copy()
        params.pop('page', None)
        params.pop('confetti', None)
        request.user.ticket_list_filter = params.urlencode()
        request.user.save(update_fields=['ticket_list_filter'])
    elif not has_any_filter:
        saved = request.user.ticket_list_filter
        if saved:
            from django.http import HttpResponseRedirect
            from django.http import QueryDict
            saved_params = QueryDict(saved, mutable=True)
            if 'confetti' in saved_params:
                saved_params.pop('confetti')
                saved = saved_params.urlencode()
                request.user.ticket_list_filter = saved
                request.user.save(update_fields=['ticket_list_filter'])
            confetti = '&confetti=1' if request.GET.get('confetti') == '1' else ''
            return HttpResponseRedirect(f'{request.path}?{saved}{confetti}')
        assignee_list = ['me']
    search = request.GET.get('q', '')
    col_id = request.GET.get('col_id', '').strip()
    col_subject = request.GET.get('col_subject', '').strip()
    col_requester = request.GET.get('col_requester', '').strip()

    if 'active' in statuses:
        qs = qs.exclude(status__in=Ticket.TERMINAL_STATUSES)
    elif statuses:
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

    # Sorting
    _SORT_MAP = {
        'id':        'pk',
        'requester': 'requester_name',
        'status':    'status',
        'assignee':  'assignee__display_name',
        'sla':       'sla_deadline',
        'created':   'created_at',
    }
    sort_by  = request.GET.get('sort', 'created')
    sort_dir = request.GET.get('dir',  'desc')
    if sort_by not in _SORT_MAP:
        sort_by, sort_dir = 'created', 'desc'
    order_expr = F(_SORT_MAP[sort_by])
    qs = qs.order_by(
        order_expr.asc(nulls_last=True) if sort_dir == 'asc' else order_expr.desc(nulls_last=True)
    )

    def _sort_url(key):
        params = request.GET.copy()
        params['sort'] = key
        params['dir'] = ('desc' if sort_dir == 'asc' else 'asc') if sort_by == key else 'asc'
        params.pop('page', None)
        return '?' + params.urlencode()

    # Paginate — 25 tickets per page
    from django.core.paginator import Paginator
    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'tickets': page_obj,
        'page_obj': page_obj,
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
        'sort_by':   sort_by,
        'sort_dir':  sort_dir,
        'sort_urls': {k: _sort_url(k) for k in _SORT_MAP},
        'max_ticket_pk': Ticket.objects.order_by('-pk').values_list('pk', flat=True).first() or 0,
        'sound_choices': request.user.NOTIFICATION_SOUND_CHOICES,
    }
    return render(request, 'tickets/list.html', context)


# ── Ticket Detail ─────────────────────────────────────────────────────────────

@admin_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related('assignee', 'category', 'subcategory', 'ticket_item'), pk=pk
    )
    comment_form = CommentForm()
    note_form = CommentForm()
    update_form = TicketUpdateForm(instance=ticket)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'comment':
            comment_form = CommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.ticket = ticket
                comment.author = request.user
                comment.is_internal = False
                comment.save()
                ticket.updated_at = timezone.now()
                ticket.save(update_fields=['updated_at'])
                from tasks.scheduled import send_ticket_notification, send_requester_comment
                # Notify assignee of update
                if ticket.assignee and ticket.assignee != request.user and ticket.assignee.notify_on_update:
                    send_ticket_notification.delay('update', ticket.pk, request.user.pk)
                # Notify requester
                if ticket.requester_email:
                    send_requester_comment.delay(ticket.pk, comment.pk)
                messages.success(request, 'Comment added.')
                return redirect('ticket_detail', pk=pk)

        elif action == 'internal_note':
            note_form = CommentForm(request.POST)
            if note_form.is_valid():
                note = note_form.save(commit=False)
                note.ticket = ticket
                note.author = request.user
                note.is_internal = True
                note.save()
                # Fire mention notifications — check each admin's exact @DisplayName
                from tasks.scheduled import notify_mention
                for admin in User.objects.filter(is_admin=True, is_active=True).exclude(pk=request.user.pk):
                    if admin.display_name and f'@{admin.display_name}' in note.body:
                        notify_mention.delay(ticket.pk, note.pk, admin.pk, request.user.pk)
                messages.success(request, 'Internal note saved.')
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
                    # Handle optional description edit submitted by admin
                    if 'description' in request.POST:
                        new_desc = request.POST.get('description', '')
                        if new_desc != ticket.description:
                            updated.description = new_desc
                            updated.description_is_html = False
                    # Stamp resolved_at when closed
                    if updated.status in Ticket.TERMINAL_STATUSES and not updated.resolved_at:
                        updated.resolved_at = timezone.now()
                    # SLA pause/unpause on status change
                    if updated.status != old_status:
                        now = timezone.now()
                        entering_pause = updated.status in Ticket.SLA_PAUSED_STATUSES
                        leaving_pause = old_status in Ticket.SLA_PAUSED_STATUSES
                        if entering_pause and not ticket.sla_paused_at:
                            updated.sla_paused_at = now
                        elif leaving_pause and ticket.sla_paused_at and updated.sla_deadline:
                            # Shift deadline forward by however long we were paused
                            updated.sla_deadline += (now - ticket.sla_paused_at)
                            updated.sla_paused_at = None
                    # Apply category fields before the single save
                    def _to_int(v):
                        try: return int(v) if v else None
                        except (ValueError, TypeError): return None
                    cat_id  = _to_int(request.POST.get('cat_id'))
                    sub_id  = _to_int(request.POST.get('sub_id'))
                    item_id = _to_int(request.POST.get('item_id'))
                    logger.info('[ticket_detail] update pk=%s cat_id=%s sub_id=%s item_id=%s', pk, cat_id, sub_id, item_id)
                    if cat_id is not None or sub_id is not None:
                        old_sub_id = ticket.subcategory_id
                        updated.category_id    = cat_id
                        updated.subcategory_id = sub_id
                        updated.ticket_item_id = item_id
                        # Auto-assign when subcategory is explicitly changed
                        if sub_id and sub_id != old_sub_id:
                            from .models import TicketSubCategory
                            sub_obj = TicketSubCategory.objects.filter(pk=sub_id).select_related('assignee').first()
                            if sub_obj and sub_obj.assignee_id:
                                updated.assignee = sub_obj.assignee
                                logger.info('[ticket_detail] auto-assigned pk=%s to %s', pk, sub_obj.assignee)
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
                    if updated.description != ticket.description:
                        history_entries.append(TicketHistory(
                            ticket=updated,
                            changed_by=request.user,
                            field='Description',
                            old_value='',
                            new_value='(updated)',
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
                    just_closed = updated.status in Ticket.TERMINAL_STATUSES and not was_closed
                    next_val = request.POST.get('next', '')
                    if next_val == 'list':
                        suffix = '?confetti=1' if just_closed else ''
                        return redirect(f'/tickets/{suffix}')
                    if next_val == 'kb' and just_closed:
                        from kb.models import KBArticle
                        existing = KBArticle.objects.filter(source_ticket=updated).first()
                        if existing:
                            messages.info(request, 'A KB article already exists for this ticket — opening it.')
                            return redirect('kb_edit', pk=existing.pk)
                        try:
                            is_hr = bool(updated.subcategory and updated.subcategory.category.name == 'HR')
                        except Exception:
                            is_hr = False
                        if not is_hr and updated.solution.strip():
                            article = KBArticle.objects.create(
                                title=updated.title,
                                body=updated.description or '',
                                solution=updated.solution,
                                subcategory_id=updated.subcategory_id,
                                ticket_item_id=updated.ticket_item_id,
                                source_ticket=updated,
                                author=request.user,
                                status=KBArticle.STATUS_DRAFT,
                            )
                            messages.success(request, 'Saved as KB draft. Review the article and publish when ready.')
                            return redirect(f'/kb/{article.pk}/edit/?confetti=1')
                    suffix = '?confetti=1' if just_closed else ''
                    return redirect(f'/tickets/{pk}/{suffix}')

        elif action == 'upload':
            file_obj = request.FILES.get('file')
            if file_obj:
                from kdesk.upload_utils import allowed_upload
                err = allowed_upload(file_obj.name)
                if err:
                    messages.error(request, err)
                    return redirect('ticket_detail', pk=pk)
                if file_obj.size > 3 * 1024 * 1024:
                    messages.error(request, 'File exceeds the 3 MB limit. Please upload a smaller file.')
                    return redirect('ticket_detail', pk=pk)
                att = TicketAttachment(
                    ticket=ticket,
                    filename=file_obj.name,
                    file=file_obj,
                    file_size=file_obj.size,
                    uploaded_by=request.user,
                )
                att.save()
                messages.success(request, f'Attachment "{file_obj.name}" uploaded.')
                return redirect('ticket_detail', pk=pk)

    mention_admins = list(
        User.objects.filter(is_admin=True, is_active=True)
        .exclude(display_name='')
        .exclude(display_name__isnull=True)
        .values('pk', 'display_name')
        .order_by('display_name')
    )
    non_inline = ticket.non_inline_attachments.select_related('uploaded_by').order_by('uploaded_at')
    my_attachments   = [a for a in non_inline if a.uploaded_by_id == request.user.pk]
    user_attachments = [a for a in non_inline if a.uploaded_by_id != request.user.pk]
    try:
        _is_hr = bool(ticket.subcategory_id and ticket.subcategory.category.name == 'HR')
    except Exception:
        _is_hr = False
    kb_prompt_eligible = (
        ticket.status not in Ticket.TERMINAL_STATUSES
        and not ticket.kb_articles.exists()
        and not _is_hr
    )
    context = {
        'ticket': ticket,
        'comment_form': comment_form,
        'note_form': note_form,
        'public_comments': ticket.comments.filter(is_internal=False).select_related('author').order_by('created_at'),
        'internal_notes': ticket.comments.filter(is_internal=True).select_related('author').order_by('created_at'),
        'update_form': update_form,
        'categories_json': _get_categories_json(),
        'ticket_history': ticket.history.select_related('changed_by').all(),
        'mention_admins_json': json.dumps([{'id': a['pk'], 'name': a['display_name']} for a in mention_admins]),
        'my_attachments': my_attachments,
        'user_attachments': user_attachments,
        'kb_prompt_eligible': kb_prompt_eligible,
    }
    return render(request, 'tickets/detail.html', context)


# ── Create Ticket ─────────────────────────────────────────────────────────────

@admin_required
def ticket_create(request):
    form = TicketForm()
    if request.method == 'POST':
        form = TicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.source = Ticket.SOURCE_MANUAL
            if ticket.requester_email and not ticket.requester_department:
                from users.models import User as UserModel
                try:
                    ru = UserModel.objects.get(email__iexact=ticket.requester_email)
                    ticket.requester_department = ru.department
                except UserModel.DoesNotExist:
                    pass
            _set_default_category(ticket)
            ticket.save()
            uploaded_file = request.FILES.get('attachment')
            if uploaded_file:
                from kdesk.upload_utils import allowed_upload
                err = allowed_upload(uploaded_file.name)
                if err:
                    messages.error(request, err)
                elif uploaded_file.size > 3 * 1024 * 1024:
                    messages.error(request, 'Attachment exceeds the 3 MB limit and was not saved.')
                else:
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
            try:
                if ticket.assignee and ticket.assignee.notify_on_assign:
                    from tasks.scheduled import send_ticket_notification
                    send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
                from tasks.scheduled import send_requester_created, generate_ai_summary
                send_requester_created.delay(ticket.pk)
                generate_ai_summary.delay(ticket.pk)
            except Exception:
                logger.exception('[ticket_create] Celery task dispatch failed for ticket #%s', ticket.pk)
            messages.success(request, f'Ticket #{ticket.pk:04d} created.')
            return redirect('ticket_detail', pk=ticket.pk)

    return render(request, 'tickets/create.html', {'form': form})


@admin_required
def lookup_user_by_email(request):
    email = request.GET.get('email', '').strip()
    if not email:
        return JsonResponse({'name': ''})
    user = User.objects.filter(email__iexact=email).first()
    return JsonResponse({'name': user.display_name or '' if user else ''})


@admin_required
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


@admin_required
@require_POST
def ticket_bulk_action(request):
    ticket_ids = [tid for tid in request.POST.getlist('ticket_ids') if str(tid).isdigit()]
    action = request.POST.get('action')

    if not ticket_ids:
        messages.warning(request, 'No tickets selected.')
        return redirect('ticket_list')

    qs = Ticket.objects.filter(pk__in=ticket_ids)
    count = qs.count()

    if action == 'delete':
        if not request.user.is_superuser:
            messages.error(request, 'Only superusers can delete tickets.')
            return redirect('ticket_list')
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


@admin_required
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

    from django.utils import timezone as _tz
    ticket.updated_at = _tz.now()
    update_fields = ['category', 'subcategory', 'ticket_item', 'updated_at']
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
        if ticket.assignee and ticket.assignee.notify_on_assign:
            from tasks.scheduled import send_ticket_notification
            send_ticket_notification.delay('assign', ticket.pk, request.user.pk)
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

@admin_required
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

    rated = Ticket.objects.filter(satisfaction_rating__isnull=False)
    avg_rating = None
    if rated.exists():
        from django.db.models import Avg
        avg_rating = round(rated.aggregate(avg=Avg('satisfaction_rating'))['avg'], 1)
    rated_30 = rated.filter(created_at__gte=last_30)

    context = {
        'tickets_30_count': tickets_30.count(),
        'breached_count': tickets_30.filter(sla_breached=True).count(),
        'resolved_count': resolved.count(),
        'avg_resolution_hours': avg_resolution,
        'by_status': by_status,
        'by_assignee': by_assignee,
        'avg_rating': avg_rating,
        'rating_count': rated.count(),
        'recent_ratings': rated_30.select_related('assignee').order_by('-updated_at')[:10],
    }
    return render(request, 'reports/index.html', context)


@admin_required
def export_tickets_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="kdesk_tickets.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'ID', 'Title', 'Status', 'Assignee',
        'Requester Email', 'Requester Name', 'Source',
        'Created At', 'Resolved At', 'SLA Deadline', 'SLA Breached',
    ])

    for t in Ticket.objects.select_related('assignee').iterator(chunk_size=500):
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

@admin_required
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

        elif action == 'sla_config':
            try:
                work_start = int(request.POST.get('sla_work_start', '8'))
                work_end   = int(request.POST.get('sla_work_end',   '17'))
                sla_hours  = float(request.POST.get('sla_hours',    '9'))
                work_days  = request.POST.get('sla_work_days', '6,0,1,2,3')
                if not (0 <= work_start < work_end <= 24 and sla_hours > 0):
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, 'Invalid SLA configuration values.')
                return redirect('settings')
            SystemSetting.set('sla_work_start', str(work_start))
            SystemSetting.set('sla_work_end',   str(work_end))
            SystemSetting.set('sla_hours',      str(sla_hours))
            SystemSetting.set('sla_work_days',  work_days)
            messages.success(request, 'SLA configuration saved.')
            return redirect('settings')

        elif action == 'emails_toggle':
            val = '1' if 'emails_enabled' in request.POST else '0'
            SystemSetting.set('emails_enabled', val)
            if val == '1':
                messages.success(request, 'Email processes re-enabled.')
            else:
                messages.warning(request, 'All email processes disabled. No emails will be sent or polled.')
            return redirect('settings')

        elif action == 'change_broadcast':
            il_email = request.POST.get('change_broadcast_il', '').strip()
            global_email = request.POST.get('change_broadcast_global', '').strip()
            SystemSetting.set('change_broadcast_il', il_email)
            SystemSetting.set('change_broadcast_global', global_email)
            messages.success(request, 'Broadcast email addresses saved.')
            return redirect('settings')

        elif action == 'sla_resume':
            from tickets.sla import business_hours_elapsed, add_business_hours, get_sla_hours
            from django.utils.dateparse import parse_datetime

            pause_started_at = parse_datetime(SystemSetting.get('sla_pause_started_at', ''))
            resume_time = timezone.now()
            sla_hours = get_sla_hours()

            if pause_started_at:
                open_tickets = Ticket.objects.filter(
                    sla_deadline__isnull=False
                ).exclude(status__in=Ticket.TERMINAL_STATUSES)
                for ticket in open_tickets:
                    elapsed_at_pause = business_hours_elapsed(ticket.created_at, pause_started_at)
                    remaining = max(0.0, sla_hours - elapsed_at_pause)
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

    categories = TicketCategory.objects.prefetch_related(
        'subcategories__items', 'subcategories__assignee'
    ).all()
    admins_qs = User.objects.filter(is_admin=True, is_active=True).order_by('display_name')
    admins_json = json.dumps([[str(a.pk), a.display_name or a.email] for a in admins_qs])

    context = {
        'servicedesk_email': settings.SERVICEDESK_EMAIL,
        'notify_requester_on_close': SystemSetting.get('notify_requester_on_close', '1') == '1',
        'sla_paused': sla_paused,
        'sla_pause_reason': sla_pause_reason,
        'sla_pause_started': sla_pause_started,
        'sla_work_start': SystemSetting.get('sla_work_start', '8'),
        'sla_work_end':   SystemSetting.get('sla_work_end',   '17'),
        'sla_hours':      SystemSetting.get('sla_hours',      '9'),
        'sla_work_days':  SystemSetting.get('sla_work_days',  '6,0,1,2,3'),
        'change_broadcast_il':     SystemSetting.get('change_broadcast_il',     'IL_All_Employees@kramerav.com'),
        'change_broadcast_global': SystemSetting.get('change_broadcast_global', 'GLOBAL_All_Employees@kramerav.com'),
        'emails_enabled': SystemSetting.get('emails_enabled', '1') == '1',
        'categories': categories,
        'admins': admins_qs,
        'admins_json': admins_json,
        'sound_choices': request.user.NOTIFICATION_SOUND_CHOICES,
    }
    return render(request, 'settings.html', context)


# ── Ticket email correspondence ───────────────────────────────────────────────

@admin_required
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

    from django.utils.html import escape as _esc
    html_body = f"""
    <p>{_esc(body).replace(chr(10), '<br>')}</p>
    <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
    <p style="color:#888;font-size:12px;">
      {_esc(sender_name)} · IT Support Team<br>
      Ticket reference: <strong>#{ticket.pk:04d}</strong>
    </p>
    """

    if SystemSetting.get('emails_enabled', '1') != '1':
        messages.warning(request, 'Email sending is currently disabled. Re-enable it in Settings.')
        return redirect('ticket_detail', pk=pk)

    cc_raw = request.POST.get('cc_emails', '')
    cc_emails = [e.strip() for e in cc_raw.split(',') if e.strip()] if cc_raw else []

    att_bytes = att_name = att_content_type = None
    uploaded_file = request.FILES.get('email_attachment')
    if uploaded_file:
        if uploaded_file.size > 3 * 1024 * 1024:
            messages.error(request, 'Email attachment exceeds the 3 MB limit.')
            return redirect('ticket_detail', pk=pk)
        att_name = uploaded_file.name
        att_bytes = uploaded_file.read()
        att_content_type = uploaded_file.content_type or 'application/octet-stream'

    try:
        from integrations.graph_client import get_client
        client = get_client()
        client.send_email(
            from_mailbox=settings.SERVICEDESK_EMAIL,
            to_email=to_email,
            subject=subject,
            body_html=html_body,
            cc_emails=cc_emails or None,
            attachments=[{'name': att_name, 'content_bytes': att_bytes, 'content_type': att_content_type}] if att_bytes else None,
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

    if att_bytes:
        from django.core.files.base import ContentFile
        TicketAttachment.objects.create(
            ticket=ticket,
            filename=att_name,
            file=ContentFile(att_bytes, name=att_name),
            file_size=len(att_bytes),
            uploaded_by=request.user,
        )

    messages.success(request, f'Email sent to {to_email}.')
    return redirect('ticket_detail', pk=pk)


# ── Attachment download (proxy — avoids public blob URL) ─────────────────────

def download_attachment(request, pk):
    """Stream an attachment through Django so Azure private blobs are accessible."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    att = get_object_or_404(TicketAttachment, pk=pk)
    ticket = att.ticket
    is_admin = getattr(request.user, 'is_admin', False) or request.user.is_superuser
    is_requester = request.user.email.lower() == (ticket.requester_email or '').lower()
    if not (is_admin or is_requester):
        return HttpResponseForbidden()
    inline = request.GET.get('inline') == '1'
    import mimetypes
    content_type, _ = mimetypes.guess_type(att.filename)
    content_type = content_type or 'application/octet-stream'
    response = FileResponse(att.file.open('rb'), content_type=content_type)
    if not inline:
        response['Content-Disposition'] = f'attachment; filename="{att.filename}"'
    return response


# ── New-ticket poll ──────────────────────────────────────────────────────────

@admin_required
def ticket_poll_new(request):
    try:
        after_id = int(request.GET.get('after_id', 0) or 0)
    except (ValueError, TypeError):
        after_id = 0
    qs = Ticket.objects.filter(pk__gt=after_id)

    statuses = request.GET.getlist('status')
    assignee_list = request.GET.getlist('assignee')
    sla_list = request.GET.getlist('sla')

    if 'active' in statuses:
        qs = qs.exclude(status__in=Ticket.TERMINAL_STATUSES)
    elif statuses:
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

    count = qs.count()
    latest_pk = qs.order_by('-pk').values_list('pk', flat=True).first() or after_id
    return JsonResponse({
        'count': count,
        'latest_pk': latest_pk,
        'sound': request.user.notification_sound,
    })


# ── Save notification sound preference ───────────────────────────────────────

@admin_required
@require_POST
def save_notification_sound(request):
    from users.models import User
    sound = request.POST.get('sound', '')
    valid = [c[0] for c in User.NOTIFICATION_SOUND_CHOICES]
    if sound not in valid:
        return JsonResponse({'ok': False, 'error': 'Invalid sound.'})
    target_pk = request.POST.get('user_pk')
    if target_pk and request.user.is_superuser:
        try:
            target = User.objects.get(pk=int(target_pk))
        except (User.DoesNotExist, ValueError):
            return JsonResponse({'ok': False, 'error': 'User not found.'})
    else:
        target = request.user
    target.notification_sound = sound
    target.save(update_fields=['notification_sound'])
    return JsonResponse({'ok': True})


# ── Edit comment ─────────────────────────────────────────────────────────────

@admin_required
@require_POST
def edit_comment(request, pk):
    from .models import TicketComment
    comment = get_object_or_404(TicketComment, pk=pk)
    new_body = request.POST.get('body', '').strip()
    if not new_body:
        return JsonResponse({'ok': False, 'error': 'Cannot be empty.'})
    old_body = comment.body
    comment.body = new_body
    comment.updated_at = timezone.now()
    comment.save(update_fields=['body', 'updated_at'])
    label = 'Internal note edited' if comment.is_internal else 'Comment edited'
    TicketHistory.objects.create(
        ticket=comment.ticket,
        changed_by=request.user,
        field=label,
        old_value=old_body[:1000],
        new_value=new_body[:1000],
    )
    return JsonResponse({'ok': True, 'body': new_body})


# ── Email preview (superuser only) ───────────────────────────────────────────

@admin_required
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
                header_color='#BE0078',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>This ticket is at <strong>78% of its SLA window</strong>. Please respond soon to avoid a breach.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails', '#BE0078') +
                    _row('SLA Deadline', '18 Apr 2026 14:00', '#BE0078') +
                    _row('Elapsed', '78%', '#BE0078')
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
                header_color='#BE0078',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>The following ticket has <strong>breached its SLA deadline</strong> and requires your immediate attention.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails', '#BE0078') +
                    _row('Requester', 'David Levi (dlevi@kramerav.com)', '#BE0078') +
                    _row('SLA Deadline', '17 Apr 2026 14:00', '#BE0078')
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
                greeting='Hi <strong>David Levi</strong>,<br><br>Your support ticket has been resolved and closed. We\'d love to hear how we did — please take a moment to rate your experience.',
                body_rows=(
                    _row('Ticket #', '#0042') +
                    _row('Subject', 'Outlook not syncing emails') +
                    _row('Closed', '17 Apr 2026 11:20') +
                    _row('Resolution', 'Reconfigured Exchange profile and cleared local cache. Emails are now syncing correctly.')
                ),
                cta_url='https://kdesk.kramerav.com/portal/tickets/42/',
                cta_label='⭐ Rate Your Experience',
            ),
        },
        {
            'label': 'Requester — New Reply from Admin',
            'html': _email_html(
                header_title='New reply on your ticket',
                header_subtitle='Ticket #0042 — Outlook not syncing emails',
                greeting='Hi <strong>David Levi</strong>,<br><br><strong>Omri Cohen</strong> from the IT team has posted a reply on your support ticket.',
                body_rows=(
                    _row('Ticket #', '#0042') +
                    _row('Subject', 'Outlook not syncing emails') +
                    _row('Reply', 'We have checked your mailbox settings. Please try removing and re-adding your account in Outlook and let us know if the issue persists.')
                ),
            ),
        },
        {
            'label': 'Admin — Ticket Closed by Requester (self-close)',
            'html': _email_html(
                header_title='Ticket Closed by Requester',
                header_subtitle='#0042 — Outlook not syncing emails',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br><strong>David Levi</strong> has self-closed their ticket, indicating they resolved the issue on their own. No further action is needed.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails') +
                    _row('Requester', 'David Levi (dlevi@kramerav.com)') +
                    _row('Closed by', 'David Levi')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='View Ticket',
            ),
        },
        {
            'label': 'Admin — @Mention in Internal Note',
            'html': _email_html(
                header_title='You were mentioned in an internal note',
                header_subtitle='#0042 — Outlook not syncing emails',
                greeting='Hi <strong>Shahar Dekner</strong>,<br><br><strong>Omri Cohen</strong> mentioned you in an internal note on ticket <strong>#0042</strong>.',
                body_rows=(
                    _row('Ticket', '#0042 — Outlook not syncing emails') +
                    _row('Requester', 'David Levi (dlevi@kramerav.com)') +
                    _row('Note', '@Shahar can you check if the Exchange connector is misconfigured on this user\'s account?')
                ),
                cta_url=f'{site}/tickets/42/',
                cta_label='View Ticket',
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
                header_color='#69FFC3',
                header_text_color='#1a1a2e',
                greeting='Hi <strong>Omri Cohen</strong>,<br><br>Your change request has been <strong>approved</strong>. You may now proceed with implementation.',
                body_rows=(
                    _row('Change', '#0007 — Firewall rule update — DMZ segment') +
                    _row('Planned Date', '20 Apr 2026  22:00 – 23:00')
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
                    '<a href="mailto:servicedesk@kramerav.com" style="color:#8205B4;">servicedesk@kramerav.com</a>.'
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

    if request.method == 'POST':
        try:
            idx = int(request.POST.get('idx', 0))
            html = samples[idx]['html']
            label = samples[idx]['label']
        except (IndexError, ValueError):
            messages.error(request, 'Invalid sample index.')
            return redirect('email_preview')
        try:
            from integrations.graph_client import get_client
            client = get_client()
            client.send_email(
                from_mailbox=settings.SERVICEDESK_EMAIL,
                to_email=request.user.email,
                subject=f'[Kdesk Preview] {label}',
                body_html=html,
            )
            messages.success(request, f'Preview sent to {request.user.email}.')
        except Exception as exc:
            messages.error(request, f'Failed to send: {exc}')
        return redirect('email_preview')

    idx = request.GET.get('idx')
    if idx is not None:
        try:
            from django.http import HttpResponse
            response = HttpResponse(samples[int(idx)]['html'])
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
        except (IndexError, ValueError):
            from django.http import Http404
            raise Http404

    return render(request, 'tickets/email_preview.html', {
        'samples': [(i, s['label']) for i, s in enumerate(samples)],
    })


# ── Categories API ────────────────────────────────────────────────────────────

@admin_required
def categories_api(request):
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': 'Superuser required'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action', '')

    if action == 'cat_add':
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Name required'})
        if TicketCategory.objects.filter(name__iexact=name).exists():
            return JsonResponse({'ok': False, 'error': 'Category already exists'})
        cat = TicketCategory.objects.create(name=name)
        return JsonResponse({'ok': True, 'id': cat.pk, 'name': cat.name})

    elif action == 'cat_rename':
        try:
            cat = TicketCategory.objects.get(pk=data.get('id'))
        except TicketCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Name required'})
        cat.name = name
        cat.save()
        return JsonResponse({'ok': True})

    elif action == 'cat_delete':
        try:
            TicketCategory.objects.get(pk=data.get('id')).delete()
        except TicketCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        return JsonResponse({'ok': True})

    elif action == 'subcat_add':
        try:
            cat = TicketCategory.objects.get(pk=data.get('cat_id'))
        except TicketCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Category not found'})
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Name required'})
        if TicketSubCategory.objects.filter(category=cat, name__iexact=name).exists():
            return JsonResponse({'ok': False, 'error': 'Subcategory already exists'})
        sub = TicketSubCategory.objects.create(category=cat, name=name)
        return JsonResponse({'ok': True, 'id': sub.pk, 'name': sub.name})

    elif action == 'subcat_update':
        try:
            sub = TicketSubCategory.objects.get(pk=data.get('id'))
        except TicketSubCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        name = data.get('name', '').strip()
        if name:
            sub.name = name
        if 'assignee_id' in data:
            if not data['assignee_id']:
                sub.assignee = None
            else:
                try:
                    sub.assignee = User.objects.get(pk=int(data['assignee_id']), is_admin=True)
                except (User.DoesNotExist, ValueError, TypeError):
                    pass
        sub.save()
        return JsonResponse({'ok': True})

    elif action == 'subcat_delete':
        try:
            TicketSubCategory.objects.get(pk=data.get('id')).delete()
        except TicketSubCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        return JsonResponse({'ok': True})

    elif action == 'item_add':
        try:
            sub = TicketSubCategory.objects.get(pk=data.get('subcat_id'))
        except TicketSubCategory.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Subcategory not found'})
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Name required'})
        if TicketItem.objects.filter(subcategory=sub, name__iexact=name).exists():
            return JsonResponse({'ok': False, 'error': 'Item already exists'})
        item = TicketItem.objects.create(subcategory=sub, name=name)
        return JsonResponse({'ok': True, 'id': item.pk, 'name': item.name})

    elif action == 'item_rename':
        try:
            item = TicketItem.objects.get(pk=data.get('id'))
        except TicketItem.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Name required'})
        item.name = name
        item.save()
        return JsonResponse({'ok': True})

    elif action == 'item_delete':
        try:
            TicketItem.objects.get(pk=data.get('id')).delete()
        except TicketItem.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Not found'})
        return JsonResponse({'ok': True})

    return JsonResponse({'ok': False, 'error': 'Unknown action'}, status=400)


# ── Employee Portal ───────────────────────────────────────────────────────────

@portal_required
def portal_dashboard(request):
    qs = (
        Ticket.objects
        .filter(requester_email__iexact=request.user.email)
        .order_by('-created_at')
    )
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )
    return render(request, 'portal/dashboard.html', {'tickets': qs, 'search_q': q})


@portal_required
def portal_ticket_create(request):
    if request.method == 'POST':
        form = PortalTicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.source = Ticket.SOURCE_MANUAL
            ticket.requester_email = request.user.email
            ticket.requester_name = request.user.display_name or ''
            ticket.requester_department = request.user.department or ''
            _set_default_category(ticket)
            ticket.save()
            uploaded_file = request.FILES.get('attachment')
            if uploaded_file:
                from kdesk.upload_utils import allowed_upload
                err = allowed_upload(uploaded_file.name)
                if err:
                    messages.error(request, err)
                elif uploaded_file.size > 3 * 1024 * 1024:
                    messages.error(request, 'Attachment exceeds the 3 MB limit and was not saved.')
                else:
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
                new_value=f'By {request.user} (portal)',
            )
            try:
                from tasks.scheduled import send_requester_created, generate_ai_summary
                send_requester_created.delay(ticket.pk)
                generate_ai_summary.delay(ticket.pk)
            except Exception:
                logger.exception('[portal_ticket_create] Celery task dispatch failed for ticket #%s', ticket.pk)
            messages.success(request, f'Ticket #{ticket.pk:04d} submitted. We\'ll be in touch soon.')
            return redirect('portal_ticket_detail', pk=ticket.pk)
    else:
        form = PortalTicketForm()
    return render(request, 'portal/create.html', {'form': form})


@portal_required
def portal_ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if ticket.requester_email.lower() != request.user.email.lower():
        messages.error(request, 'Ticket not found.')
        return redirect('portal_dashboard')

    comment_form = CommentForm()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'comment':
            comment_form = CommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.ticket = ticket
                comment.author = request.user
                comment.is_internal = False
                comment.save()
                if ticket.status not in Ticket.TERMINAL_STATUSES:
                    ticket.status = Ticket.STATUS_USER_RESPONDED
                ticket.updated_at = timezone.now()
                ticket.save(update_fields=['status', 'updated_at'])
                if ticket.assignee and ticket.assignee.notify_on_update:
                    from tasks.scheduled import send_ticket_notification
                    send_ticket_notification.delay('update', ticket.pk, request.user.pk)
                messages.success(request, 'Reply sent.')
                return redirect('portal_ticket_detail', pk=pk)

        elif action == 'self_close' and ticket.status not in Ticket.TERMINAL_STATUSES:
            ticket.status = Ticket.STATUS_CLOSED
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
            from tasks.scheduled import notify_user_closed_ticket
            notify_user_closed_ticket.delay(ticket.pk, request.user.pk)
            messages.success(request, 'Your ticket has been closed. Glad you sorted it out!')
            return redirect('portal_ticket_detail', pk=pk)

        elif action == 'rate' and ticket.status in Ticket.TERMINAL_STATUSES and not ticket.satisfaction_rating:
            try:
                rating = int(request.POST.get('rating', 0))
            except (ValueError, TypeError):
                rating = 0
            if 1 <= rating <= 5:
                ticket.satisfaction_rating = rating
                ticket.satisfaction_text = request.POST.get('rating_text', '')[:50].strip()
                ticket.save(update_fields=['satisfaction_rating', 'satisfaction_text'])
                messages.success(request, 'Thank you for your feedback!')
            return redirect('portal_ticket_detail', pk=pk)

    comments = (
        ticket.comments
        .filter(is_internal=False)
        .select_related('author')
        .order_by('created_at')
    )
    can_reply = ticket.status not in Ticket.TERMINAL_STATUSES
    can_close = can_reply
    can_rate   = (ticket.status in Ticket.TERMINAL_STATUSES and not ticket.satisfaction_rating)

    return render(request, 'portal/detail.html', {
        'ticket': ticket,
        'comments': comments,
        'comment_form': comment_form,
        'can_reply': can_reply,
        'can_close': can_close,
        'can_rate': can_rate,
    })


@admin_required
def portal_preview_enter(request):
    request.session['portal_preview'] = True
    return redirect('portal_dashboard')


@admin_required
def portal_preview_exit(request):
    request.session.pop('portal_preview', None)
    return redirect('dashboard')


# ── SysAid CSV import (superuser only) ───────────────────────────────────────

@admin_required
def import_sysaid(request):
    if not request.user.is_superuser:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    results = None

    if request.method == 'POST' and request.FILES.get('csv_file'):
        from datetime import datetime as dt_cls
        from zoneinfo import ZoneInfo
        from django.db import transaction
        from tickets.models import TicketCategory, TicketSubCategory, TicketComment
        from tickets.sla import _get_sla_config, add_business_hours
        from users.models import User as UserModel

        STATUS_MAP = {
            'work in progress':         Ticket.STATUS_IN_PROGRESS,
            'in progress':              Ticket.STATUS_IN_PROGRESS,
            'programmer':               Ticket.STATUS_IN_PROGRESS,
            'specification':            Ticket.STATUS_IN_PROGRESS,
            'move to task list':        Ticket.STATUS_IN_PROGRESS,
            'pending user reply':       Ticket.STATUS_PENDING_USER,
            'pending for vendor action':Ticket.STATUS_PENDING_VENDOR,
            'pending vendor':           Ticket.STATUS_PENDING_VENDOR,
            'pending manager approval': Ticket.STATUS_PENDING_MANAGER,
            'hold':                     Ticket.STATUS_HOLD,
            'user responded':           Ticket.STATUS_USER_RESPONDED,
            'new':                      Ticket.STATUS_NEW,
            'closed':                   Ticket.STATUS_CLOSED,
        }

        il_tz = ZoneInfo('Asia/Jerusalem')

        def parse_dt(s):
            s = (s or '').strip()
            if not s:
                return None
            for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y'):
                try:
                    return dt_cls.strptime(s, fmt).replace(tzinfo=il_tz)
                except ValueError:
                    continue
            return None

        # ── Pre-load all lookups — one query each instead of one per row ──────
        from tickets.models import TicketItem
        categories  = {c.name.lower(): c for c in TicketCategory.objects.all()}
        subcats     = {
            (sc.category_id, sc.name.lower()): sc
            for sc in TicketSubCategory.objects.select_related('category').all()
        }
        items       = {
            (it.subcategory_id, it.name.lower()): it
            for it in TicketItem.objects.all()
        }
        admins      = {
            u.display_name.lower(): u
            for u in UserModel.objects.filter(is_admin=True)
        }
        _, _, sla_hours, _ = _get_sla_config()

        # ── Parse CSV (pure Python, no DB) ────────────────────────────────────
        csv_file = request.FILES['csv_file']
        raw_bytes = csv_file.read()
        try:
            decoded = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = raw_bytes.decode('windows-1255', errors='replace')
        reader   = csv.DictReader(io.StringIO(decoded))

        pending      = []
        skipped_rows = []

        for row in reader:
            sysaid_id = row.get('Ticket number', '').strip()
            title     = (row.get('Title') or '').strip()
            if not title:
                skipped_rows.append({'id': sysaid_id or '?', 'reason': 'No title'})
                continue

            raw_user = (row.get('Request username') or '').strip()
            if '\\' in raw_user:
                raw_user = raw_user.split('\\', 1)[1]
            requester_email = f'{raw_user}@kramerav.com' if raw_user else f'unknown-{sysaid_id}@import.local'
            requester_name  = (row.get('Submitter') or '').strip()

            raw_status = (row.get('Status') or '').strip().lower().strip()
            status     = STATUS_MAP.get(raw_status, Ticket.STATUS_NEW)
            created_at = parse_dt(row.get('Request time'))
            request_time_str = (row.get('Request time') or '').strip()

            assignee_name = (row.get('Assignee') or '').strip()
            assignee      = admins.get(assignee_name.lower()) if assignee_name else None

            cat_name   = (row.get('Category') or '').strip()
            sub_name   = (row.get('Sub-Category') or '').strip()
            third_name = (row.get('Third Level Category') or '').strip()
            category   = categories.get(cat_name.lower()) if cat_name else None
            subcat     = subcats.get((category.pk, sub_name.lower())) if category and sub_name else None
            item       = items.get((subcat.pk, third_name.lower())) if subcat and third_name else None

            # Title includes original ticket number and request time
            full_title = f'[SysAid #{sysaid_id} | {request_time_str}] {title}' if sysaid_id else title

            # Description prefixed with SysAid origin block
            raw_desc = (row.get('Description') or '').strip()
            origin_line = f'[Imported from SysAid — Ticket #{sysaid_id}, submitted {request_time_str} by {requester_name}]'
            full_desc = f'{origin_line}\n\n{raw_desc}' if raw_desc else origin_line

            sla_ref      = created_at if created_at else timezone.now()
            sla_deadline = add_business_hours(sla_ref, sla_hours)

            pending.append({
                'sysaid_id':           sysaid_id,
                'title':               full_title,
                'description':         full_desc,
                'status':              status,
                'requester_email':     requester_email,
                'requester_name':      requester_name,
                'requester_department': '',
                'assignee':            assignee,
                'category':            category,
                'subcategory':         subcat,
                'ticket_item':         item,
                'sla_deadline':        sla_deadline,
                'created_at':          created_at,
            })

        # ── Build index of existing SysAid tickets for update-vs-create ─────
        existing = {}
        for t in Ticket.objects.filter(title__startswith='[SysAid #'):
            try:
                sid = t.title.split('[SysAid #')[1].split(' |')[0].strip()
                if sid:
                    existing[sid] = t
            except IndexError:
                pass

        # ── Upsert everything in one transaction ──────────────────────────────
        created_rows = []
        updated_rows = []
        with transaction.atomic():
            for r in pending:
                existing_ticket = existing.get(r['sysaid_id']) if r['sysaid_id'] else None

                if existing_ticket:
                    # Update title, description, category fields only — preserve status/assignee changes
                    existing_ticket.title              = r['title']
                    existing_ticket.description        = r['description']
                    existing_ticket.description_is_html = False
                    existing_ticket.category           = r['category']
                    existing_ticket.subcategory        = r['subcategory']
                    existing_ticket.ticket_item        = r['ticket_item']
                    existing_ticket.save(update_fields=[
                        'title', 'description', 'description_is_html',
                        'category', 'subcategory', 'ticket_item',
                    ])
                    updated_rows.append({
                        'sysaid_id': r['sysaid_id'],
                        'pk':        existing_ticket.pk,
                        'title':     r['title'],
                    })
                else:
                    ticket = Ticket(
                        title=r['title'],
                        description=r['description'],
                        description_is_html=False,
                        status=r['status'],
                        source=Ticket.SOURCE_MANUAL,
                        requester_email=r['requester_email'],
                        requester_name=r['requester_name'],
                        requester_department=r['requester_department'],
                        assignee=r['assignee'],
                        category=r['category'],
                        subcategory=r['subcategory'],
                        ticket_item=r['ticket_item'],
                        sla_deadline=r['sla_deadline'],
                    )
                    if r['status'] in Ticket.TERMINAL_STATUSES:
                        ticket.resolved_at = timezone.now()
                    ticket.save()

                    if r['created_at']:
                        Ticket.objects.filter(pk=ticket.pk).update(created_at=r['created_at'])

                    created_rows.append({
                        'sysaid_id': r['sysaid_id'],
                        'pk':        ticket.pk,
                        'title':     r['title'],
                        'requester': r['requester_email'],
                        'assignee':  str(r['assignee']) if r['assignee'] else '—',
                        'status':    ticket.get_status_display(),
                    })

        results = {'created': created_rows, 'skipped': skipped_rows, 'updated': updated_rows}

    return render(request, 'tickets/import_sysaid.html', {'results': results})
