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
    # Parsed HTML cached at upload time — list of {name, html} as JSON
    rendered_sheets = models.TextField(blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.original_name
