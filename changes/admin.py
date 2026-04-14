from django.contrib import admin
from .models import Change


@admin.register(Change)
class ChangeAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'status', 'risk_level', 'affected_system', 'planned_date', 'submitted_by']
    list_filter = ['status', 'risk_level', 'affected_system']
    search_fields = ['title', 'description']
