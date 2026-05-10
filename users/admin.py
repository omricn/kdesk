from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'display_name', 'is_admin', 'is_admin_override', 'is_active', 'last_sync')
    list_filter = ('is_admin', 'is_admin_override', 'is_active')
    search_fields = ('email', 'display_name')
    ordering = ('email',)
    readonly_fields = ('last_sync', 'date_joined', 'entra_id')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Profile', {'fields': ('display_name', 'entra_id')}),
        ('Permissions', {'fields': ('is_active', 'is_admin', 'is_admin_override', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Notifications', {'fields': ('notify_on_assign', 'notify_on_update', 'notify_on_sla_breach')}),
        ('Info', {'fields': ('last_sync', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'display_name', 'password1', 'password2', 'is_admin', 'is_staff'),
        }),
    )
