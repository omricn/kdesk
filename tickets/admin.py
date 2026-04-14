from django.contrib import admin
from .models import Ticket, TicketComment, TicketAttachment, EmailLog, SystemSetting


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    readonly_fields = ('created_at',)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    readonly_fields = ('uploaded_at', 'file_size')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('pk', 'title', 'status', 'assignee', 'requester_email', 'sla_breached', 'created_at')
    list_filter = ('status', 'sla_breached', 'source')
    search_fields = ('title', 'description', 'requester_email', 'requester_name')
    readonly_fields = ('created_at', 'updated_at', 'email_message_id', 'source')
    inlines = [TicketCommentInline, TicketAttachmentInline]
    date_hierarchy = 'created_at'


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'description')


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('message_id', 'processed_at', 'ticket', 'error')
    readonly_fields = ('message_id', 'processed_at', 'ticket', 'error')
