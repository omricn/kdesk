from django.contrib import admin
from .models import KBArticle, KBAttachment


class KBAttachmentInline(admin.TabularInline):
    model = KBAttachment
    extra = 0
    readonly_fields = ('filename', 'file_size', 'uploaded_at', 'uploaded_by')


@admin.register(KBArticle)
class KBArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'subcategory', 'ticket_item', 'status', 'author', 'updated_at')
    list_filter = ('status', 'subcategory__category')
    search_fields = ('title', 'body')
    inlines = [KBAttachmentInline]
