import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from tickets.views import admin_required
from .forms import ChangeForm
from .models import Change, ChangeAttachment


@admin_required
def change_list(request):
    from django.db.models import Q
    qs = Change.objects.select_related('submitted_by').all()

    q      = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(description__icontains=q) |
            Q(submitted_by__display_name__icontains=q) |
            Q(submitted_by__email__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    # Calendar JSON for FullCalendar
    events = []
    for c in qs:
        events.append({
            'id': c.pk,
            'title': f'[{c.get_risk_level_display()}] {c.title}',
            'start': (
                f'{c.planned_date.isoformat()}T{c.planned_from.strftime("%H:%M")}'
                if c.planned_from else c.planned_date.isoformat()
            ),
            'color': c.calendar_color,
            'url': f'/changes/{c.pk}/',
        })

    context = {
        'changes': qs,
        'events_json': json.dumps(events),
        'q': q,
        'status_filter': status,
        'status_choices': Change.STATUS_CHOICES,
    }
    return render(request, 'changes/list.html', context)


@admin_required
def change_detail(request, pk):
    change = get_object_or_404(Change.objects.select_related('submitted_by'), pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'notes':
            if request.user != change.submitted_by and not request.user.is_superuser:
                messages.error(request, 'You can only edit notes on your own changes.')
                return redirect('change_detail', pk=pk)
            notes = request.POST.get('notes', '').strip()
            change.notes = notes
            change.save(update_fields=['notes', 'updated_at'])
            messages.success(request, 'Notes saved.')
            return redirect('change_detail', pk=pk)

        elif action == 'upload_attachment':
            if change.status in (Change.STATUS_NEW, Change.STATUS_PENDING_CHANGES):
                _save_attachments(request, change)
            return redirect('change_detail', pk=pk)

        elif action == 'delete_attachment':
            att_id = request.POST.get('attachment_id')
            if request.user == change.submitted_by or request.user.is_superuser:
                ChangeAttachment.objects.filter(pk=att_id, change=change).delete()
                messages.success(request, 'Attachment removed.')
            return redirect('change_detail', pk=pk)

    is_manager = request.user.is_it_manager
    can_cancel = (request.user == change.submitted_by or request.user.is_superuser or request.user.is_it_manager)
    return render(request, 'changes/detail.html', {
        'change': change,
        'is_manager': is_manager,
        'can_cancel': can_cancel,
        'attachments': change.attachments.all(),
    })


@admin_required
def change_create(request):
    form = ChangeForm()
    if request.method == 'POST':
        form = ChangeForm(request.POST)
        if form.is_valid():
            change = form.save(commit=False)
            change.submitted_by = request.user
            change.save()
            _save_attachments(request, change)
            messages.success(request, f'Change #{change.pk:04d} created.')
            return redirect('change_detail', pk=change.pk)
    return render(request, 'changes/form.html', {'form': form, 'action': 'Create'})


@admin_required
def change_edit(request, pk):
    change = get_object_or_404(Change, pk=pk)
    if request.user != change.submitted_by and not request.user.is_superuser:
        messages.error(request, 'You can only edit your own changes.')
        return redirect('change_detail', pk=pk)
    if change.status not in (Change.STATUS_NEW, Change.STATUS_PENDING, Change.STATUS_PENDING_CHANGES):
        messages.error(request, 'Only New, Pending, or Pending Changes requests can be edited.')
        return redirect('change_detail', pk=pk)

    form = ChangeForm(instance=change)
    if request.method == 'POST':
        form = ChangeForm(request.POST, instance=change)
        if form.is_valid():
            form.save()
            _save_attachments(request, change)
            messages.success(request, 'Change updated.')
            return redirect('change_detail', pk=pk)
    return render(request, 'changes/form.html', {
        'form': form, 'action': 'Edit', 'change': change,
        'attachments': change.attachments.all(),
    })


@admin_required
@require_POST
def change_transition(request, pk):
    change = get_object_or_404(Change.objects.select_related('submitted_by'), pk=pk)
    action = request.POST.get('action')

    is_manager = request.user.is_it_manager

    # Cancel is allowed from any status except done/already-cancelled
    if action == 'cancel':
        if change.status in (Change.STATUS_DONE, Change.STATUS_CANCELLED):
            messages.error(request, 'This change cannot be cancelled.')
            return redirect('change_detail', pk=pk)
        if (request.user != change.submitted_by
                and not request.user.is_superuser
                and not request.user.is_it_manager):
            messages.error(request, 'Only the submitter or IT Manager can cancel this change.')
            return redirect('change_detail', pk=pk)
        change.status = Change.STATUS_CANCELLED
        change.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'Change #{pk:04d} has been cancelled.')
        return redirect('change_detail', pk=pk)

    transitions = {
        'submit':           (Change.STATUS_NEW,              Change.STATUS_PENDING),
        'resubmit':         (Change.STATUS_PENDING_CHANGES,  Change.STATUS_PENDING),
        'approve':          (Change.STATUS_PENDING,          Change.STATUS_APPROVED),
        'not_approve':      (Change.STATUS_PENDING,          Change.STATUS_NOT_APPROVED),
        'request_changes':  (Change.STATUS_PENDING,          Change.STATUS_PENDING_CHANGES),
        'start':            (Change.STATUS_APPROVED,         Change.STATUS_IN_PROGRESS),
        'complete':         (Change.STATUS_IN_PROGRESS,      Change.STATUS_DONE),
        'reopen':           (Change.STATUS_DONE,             Change.STATUS_NEW),
    }

    if action not in transitions:
        messages.error(request, 'Invalid action.')
        return redirect('change_detail', pk=pk)

    # Only managers can approve, reject, or request changes
    if action in ('approve', 'not_approve', 'request_changes') and not is_manager:
        messages.error(request, 'Only the IT Manager can approve or reject changes.')
        return redirect('change_detail', pk=pk)

    required_status, new_status = transitions[action]
    if change.status != required_status:
        messages.error(request, f'Cannot perform "{action}" from current status.')
        return redirect('change_detail', pk=pk)

    if action == 'request_changes':
        remarks = request.POST.get('manager_remarks', '').strip()
        if not remarks:
            messages.error(request, 'Please enter your remarks before requesting changes.')
            return redirect('change_detail', pk=pk)
        change.manager_remarks = remarks

    change.status = new_status
    change.save(update_fields=['status', 'manager_remarks', 'updated_at'])

    # Trigger notifications
    if action == 'submit':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'submitted')
        messages.success(request, 'Change submitted for approval. The IT Manager has been notified.')
    elif action == 'approve':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'approved')
        messages.success(request, 'Change approved.')
    elif action == 'not_approve':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'not_approved')
        messages.warning(request, 'Change marked as Not Approved. The submitter has been notified.')
    elif action == 'request_changes':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'changes_requested')
        messages.info(request, 'Changes requested. The submitter has been notified.')
    elif action == 'resubmit':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'resubmitted')
        messages.success(request, 'Change resubmitted for approval. The IT Manager has been notified.')
    elif action == 'complete':
        from tasks.scheduled import notify_change
        notify_change.delay(pk, 'done')
        messages.success(request, 'Change marked as done.')
    elif action == 'start':
        messages.success(request, 'Change is now in progress.')
    elif action == 'reopen':
        messages.success(request, 'Change reopened.')

    return redirect('change_detail', pk=pk)


def _save_attachments(request, change):
    """Save uploaded files from request.FILES['attachments'] to ChangeAttachment."""
    from django.contrib import messages as msg
    from kdesk.upload_utils import allowed_upload
    for f in request.FILES.getlist('attachments'):
        err = allowed_upload(f.name)
        if err:
            msg.error(request, err)
            continue
        if f.size > 3 * 1024 * 1024:
            msg.error(request, f'"{f.name}" exceeds the 3 MB limit and was skipped.')
            continue
        ChangeAttachment.objects.create(
            change=change,
            filename=f.name,
            file=f,
            file_size=f.size,
        )


@admin_required
def change_download_attachment(request, pk):
    from django.http import FileResponse
    att = get_object_or_404(ChangeAttachment, pk=pk)
    return FileResponse(att.file.open('rb'), as_attachment=True, filename=att.filename)
