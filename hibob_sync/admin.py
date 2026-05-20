from django.contrib import admin
from .models import SyncTrigger, SyncRun, SyncChange


class SyncChangeInline(admin.TabularInline):
    model = SyncChange
    extra = 0
    readonly_fields = ('email', 'field_name', 'old_value', 'new_value')


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ('completed_at', 'is_dry_run', 'matched', 'updated', 'skipped', 'not_found', 'errors', 'success')
    list_filter = ('is_dry_run', 'success')
    readonly_fields = ('trigger', 'started_at', 'completed_at', 'is_dry_run', 'matched', 'updated',
                       'skipped', 'not_found', 'errors', 'raw_log', 'success', 'error_message', 'log_filename')
    inlines = [SyncChangeInline]


@admin.register(SyncTrigger)
class SyncTriggerAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'is_dry_run', 'status', 'triggered_by', 'claimed_at', 'completed_at')
    list_filter = ('status', 'is_dry_run')
    readonly_fields = ('created_at', 'claimed_at', 'completed_at')
