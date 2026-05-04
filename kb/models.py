from django.db import models
from django.conf import settings


class KBArticle(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ]

    title = models.CharField(max_length=300)
    body = models.TextField(blank=True, default='')
    solution = models.TextField(blank=True, default='')
    subcategory = models.ForeignKey(
        'tickets.TicketSubCategory', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kb_articles',
    )
    ticket_item = models.ForeignKey(
        'tickets.TicketItem', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kb_articles',
    )
    source_ticket = models.ForeignKey(
        'tickets.Ticket', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kb_articles',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default=STATUS_DRAFT, db_index=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kb_articles',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    @property
    def body_snippet(self):
        text = self.solution or self.body
        return text[:200].strip()


class KBAttachment(models.Model):
    article = models.ForeignKey(KBArticle, on_delete=models.CASCADE, related_name='attachments')
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to='kb/%Y/%m/')
    file_size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kb_uploads',
    )

    def __str__(self):
        return self.filename
