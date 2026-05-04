import logging
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import KBArticleForm
from .models import KBArticle, KBAttachment

logger = logging.getLogger(__name__)


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'{settings.LOGIN_URL}?next={request.path}')
        if not request.user.is_admin:
            return redirect('portal_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


# ── Admin views ───────────────────────────────────────────────────────────────

@admin_required
def kb_list(request):
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '')

    articles = KBArticle.objects.select_related(
        'subcategory__category', 'ticket_item', 'author', 'source_ticket'
    )
    if q:
        articles = articles.filter(Q(title__icontains=q) | Q(body__icontains=q) | Q(solution__icontains=q))
    if status_filter in ('draft', 'published'):
        articles = articles.filter(status=status_filter)

    draft_count = KBArticle.objects.filter(status='draft').count()
    return render(request, 'kb/list.html', {
        'articles': articles,
        'q': q,
        'status_filter': status_filter,
        'draft_count': draft_count,
    })


@admin_required
def kb_create(request):
    initial = {}
    ticket_id = request.GET.get('from_ticket')
    if ticket_id:
        try:
            from tickets.models import Ticket
            ticket = Ticket.objects.select_related('subcategory__category', 'ticket_item').get(pk=ticket_id)
            initial = {
                'title': ticket.title,
                'body': ticket.description or '',
                'solution': ticket.solution or '',
                'subcategory': ticket.subcategory,
                'ticket_item': ticket.ticket_item,
                'status': KBArticle.STATUS_DRAFT,
            }
        except Exception:
            pass

    if request.method == 'POST':
        form = KBArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            article.author = request.user
            if ticket_id:
                try:
                    from tickets.models import Ticket
                    article.source_ticket = Ticket.objects.get(pk=ticket_id)
                except Exception:
                    pass
            article.save()
            _save_attachments(request, article)
            messages.success(request, 'Article created.')
            return redirect('kb_edit', pk=article.pk)
    else:
        form = KBArticleForm(initial=initial)
        if initial.get('subcategory'):
            from tickets.models import TicketItem
            form.fields['ticket_item'].queryset = TicketItem.objects.filter(
                subcategory=initial['subcategory']
            )

    return render(request, 'kb/form.html', {'form': form, 'article': None})


@admin_required
def kb_edit(request, pk):
    article = get_object_or_404(KBArticle, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete_attachment':
            att_pk = request.POST.get('attachment_pk')
            KBAttachment.objects.filter(pk=att_pk, article=article).delete()
            return redirect('kb_edit', pk=pk)

        form = KBArticleForm(request.POST, instance=article)
        if form.is_valid():
            form.save()
            _save_attachments(request, article)
            messages.success(request, 'Article saved.')
            return redirect('kb_edit', pk=pk)
    else:
        form = KBArticleForm(instance=article)
        if article.subcategory_id:
            from tickets.models import TicketItem
            form.fields['ticket_item'].queryset = TicketItem.objects.filter(
                subcategory_id=article.subcategory_id
            )

    return render(request, 'kb/form.html', {'form': form, 'article': article})


@admin_required
@require_POST
def kb_delete(request, pk):
    article = get_object_or_404(KBArticle, pk=pk)
    if not request.user.is_superuser and article.author != request.user:
        messages.error(request, 'Only the article author or a superadmin can delete this article.')
        return redirect('kb_edit', pk=pk)
    article.delete()
    messages.success(request, 'Article deleted.')
    return redirect('kb_list')


@admin_required
@require_POST
def kb_publish(request, pk):
    article = get_object_or_404(KBArticle, pk=pk)
    article.status = KBArticle.STATUS_PUBLISHED
    article.save(update_fields=['status'])
    messages.success(request, 'Article published.')
    return redirect('kb_edit', pk=pk)


@admin_required
def kb_download_attachment(request, pk):
    att = get_object_or_404(KBAttachment, pk=pk)
    return FileResponse(att.file.open('rb'), as_attachment=True, filename=att.filename)


@admin_required
@require_POST
def kb_from_ticket(request, ticket_pk):
    from tickets.models import Ticket
    ticket = get_object_or_404(
        Ticket.objects.select_related('subcategory__category', 'ticket_item'),
        pk=ticket_pk,
    )

    if not ticket.solution.strip():
        messages.warning(request, 'Add a solution to the ticket before saving to the Knowledge Base.')
        return redirect('ticket_detail', pk=ticket_pk)

    if ticket.subcategory and ticket.subcategory.category.name == 'HR':
        messages.warning(request, 'HR-category tickets are not saved to the Knowledge Base.')
        return redirect('ticket_detail', pk=ticket_pk)

    existing = KBArticle.objects.filter(source_ticket=ticket).first()
    if existing:
        messages.info(request, 'A KB article already exists for this ticket — opening it.')
        return redirect('kb_edit', pk=existing.pk)

    article = KBArticle.objects.create(
        title=ticket.title,
        body=ticket.description or '',
        solution=ticket.solution,
        subcategory=ticket.subcategory,
        ticket_item=ticket.ticket_item,
        source_ticket=ticket,
        author=request.user,
        status=KBArticle.STATUS_DRAFT,
    )
    messages.success(request, 'Saved as KB draft. Review the article and publish when ready.')
    return redirect('kb_edit', pk=article.pk)


def _save_attachments(request, article):
    from kdesk.upload_utils import allowed_upload
    for f in request.FILES.getlist('files'):
        err = allowed_upload(f.name)
        if err:
            messages.error(request, err)
            continue
        if f.size > 10 * 1024 * 1024:
            messages.error(request, f'"{f.name}" exceeds the 10 MB limit and was skipped.')
            continue
        KBAttachment.objects.create(
            article=article,
            filename=f.name,
            file=f,
            file_size=f.size,
            uploaded_by=request.user,
        )


# ── Admin: items API (for dynamic dropdown) ───────────────────────────────────

@admin_required
def kb_items_api(request):
    from django.http import JsonResponse
    from tickets.models import TicketItem
    sub_id = request.GET.get('subcategory')
    items = []
    if sub_id:
        items = list(TicketItem.objects.filter(subcategory_id=sub_id).values('id', 'name'))
    return JsonResponse({'items': items})


# ── Portal views ──────────────────────────────────────────────────────────────

@admin_required
def portal_kb(request):
    from tickets.models import TicketSubCategory
    q = request.GET.get('q', '').strip()

    if q:
        return portal_kb_search(request)

    subcategories = (
        TicketSubCategory.objects
        .select_related('category')
        .exclude(category__name='HR')
        .annotate(article_count=Count('kb_articles', filter=Q(kb_articles__status='published')))
        .filter(article_count__gt=0)
        .order_by('category__name', 'name')
    )

    grouped = {}
    for sub in subcategories:
        cat_name = sub.category.name
        if cat_name not in grouped:
            grouped[cat_name] = []
        grouped[cat_name].append(sub)

    return render(request, 'portal/kb/home.html', {
        'grouped': grouped,
        'q': q,
    })


@admin_required
def portal_kb_subcategory(request, subcategory_pk):
    from tickets.models import TicketSubCategory, TicketItem
    subcategory = get_object_or_404(
        TicketSubCategory.objects.select_related('category').exclude(category__name='HR'),
        pk=subcategory_pk,
    )

    items = (
        TicketItem.objects
        .filter(subcategory=subcategory)
        .annotate(article_count=Count('kb_articles', filter=Q(kb_articles__status='published')))
        .filter(article_count__gt=0)
        .order_by('name')
    )

    # Articles with no item under this subcategory
    uncategorized_count = KBArticle.objects.filter(
        subcategory=subcategory, ticket_item__isnull=True, status=KBArticle.STATUS_PUBLISHED
    ).count()

    return render(request, 'portal/kb/subcategory.html', {
        'subcategory': subcategory,
        'items': items,
        'uncategorized_count': uncategorized_count,
    })


@admin_required
def portal_kb_item(request, subcategory_pk, item_pk):
    from tickets.models import TicketSubCategory, TicketItem
    subcategory = get_object_or_404(
        TicketSubCategory.objects.select_related('category').exclude(category__name='HR'),
        pk=subcategory_pk,
    )
    item = get_object_or_404(TicketItem, pk=item_pk, subcategory=subcategory)

    articles = KBArticle.objects.filter(
        subcategory=subcategory, ticket_item=item, status=KBArticle.STATUS_PUBLISHED
    ).order_by('-updated_at')

    return render(request, 'portal/kb/articles.html', {
        'subcategory': subcategory,
        'item': item,
        'articles': articles,
    })


@admin_required
def portal_kb_uncategorized(request, subcategory_pk):
    from tickets.models import TicketSubCategory
    subcategory = get_object_or_404(
        TicketSubCategory.objects.select_related('category').exclude(category__name='HR'),
        pk=subcategory_pk,
    )
    articles = KBArticle.objects.filter(
        subcategory=subcategory, ticket_item__isnull=True, status=KBArticle.STATUS_PUBLISHED
    ).order_by('-updated_at')

    return render(request, 'portal/kb/articles.html', {
        'subcategory': subcategory,
        'item': None,
        'articles': articles,
    })


@admin_required
def portal_kb_article(request, pk):
    article = get_object_or_404(KBArticle, pk=pk, status=KBArticle.STATUS_PUBLISHED)
    return render(request, 'portal/kb/article.html', {'article': article})


@admin_required
def portal_kb_search(request):
    q = request.GET.get('q', '').strip()
    articles = []
    if q:
        articles = (
            KBArticle.objects
            .filter(status=KBArticle.STATUS_PUBLISHED)
            .filter(Q(title__icontains=q) | Q(body__icontains=q) | Q(solution__icontains=q))
            .select_related('subcategory__category', 'ticket_item')
            .exclude(subcategory__category__name='HR')
            .order_by('-updated_at')
        )
    return render(request, 'portal/kb/search.html', {'articles': articles, 'q': q})
