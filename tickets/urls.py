from django.urls import path
from . import views

urlpatterns = [
    # ── Admin portal ──────────────────────────────────────────────────────────
    path('', views.dashboard, name='dashboard'),
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/new/', views.ticket_create, name='ticket_create'),
    path('tickets/bulk/', views.ticket_bulk_action, name='ticket_bulk_action'),
    path('tickets/<int:pk>/', views.ticket_detail, name='ticket_detail'),
    path('tickets/<int:pk>/categorize/', views.ticket_categorize, name='ticket_categorize'),
    path('tickets/<int:pk>/send-email/', views.ticket_send_email, name='ticket_send_email'),
    path('tickets/lookup-user/', views.lookup_user_by_email, name='lookup_user_by_email'),
    path('tickets/user-search/', views.user_search, name='user_search'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.export_tickets_csv, name='export_csv'),
    path('settings/', views.settings_view, name='settings'),
    path('dev/email-preview/', views.email_preview, name='email_preview'),
    # ── Employee portal ───────────────────────────────────────────────────────
    path('portal/', views.portal_dashboard, name='portal_dashboard'),
    path('portal/new/', views.portal_ticket_create, name='portal_ticket_create'),
    path('portal/tickets/<int:pk>/', views.portal_ticket_detail, name='portal_ticket_detail'),
    path('portal/preview/enter/', views.portal_preview_enter, name='portal_preview_enter'),
    path('portal/preview/exit/', views.portal_preview_exit, name='portal_preview_exit'),
]
