from django.conf import settings
from django.db import models


class BudgetFile(models.Model):
    file = models.FileField(upload_to='budget/')
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='budget_uploads',
    )
    rendered_sheets = models.TextField(blank=True)
    is_processing = models.BooleanField(default=False)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.original_name


class BudgetConfig(models.Model):
    """Singleton — always pk=1. Stores the SharePoint URL and HTML cache."""
    sharepoint_url = models.URLField(blank=True, max_length=1000)
    web_url = models.URLField(blank=True, max_length=1000)   # direct Excel Online URL from Graph
    embed_url = models.URLField(blank=True, max_length=2000)  # Doc.aspx iframe-safe embed URL
    cached_sheets   = models.TextField(blank=True)   # JSON [{name, html}, ...]
    cache_updated_at = models.DateTimeField(null=True, blank=True)
    configured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='budget_configs',
    )

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def cache_is_fresh(self, minutes=60):
        if not self.cache_updated_at:
            return False
        from django.utils import timezone
        from datetime import timedelta
        return (timezone.now() - self.cache_updated_at) < timedelta(minutes=minutes)

    def __str__(self):
        return 'Budget Configuration'
