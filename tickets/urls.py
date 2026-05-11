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
    path('tickets/<int:pk>/set-requester/', views.ticket_set_requester, name='ticket_set_requester'),
    path('tickets/<int:pk>/send-email/', views.ticket_send_email, name='ticket_send_email'),
    path('attachments/<int:pk>/download/', views.download_attachment, name='download_attachment'),
    path('attachments/<int:pk>/delete/', views.delete_attachment, name='delete_attachment'),
    path('tickets/<int:pk>/solution-image/', views.solution_image_upload, name='solution_image_upload'),
    path('comments/<int:pk>/edit/', views.edit_comment, name='edit_comment'),
    path('tickets/poll-new/', views.ticket_poll_new, name='ticket_poll_new'),
    path('tickets/notification-sound/', views.save_notification_sound, name='save_notification_sound'),
    path('tickets/lookup-user/', views.lookup_user_by_email, name='lookup_user_by_email'),
    path('tickets/user-search/', views.user_search, name='user_search'),
    path('tickets/merge-search/', views.ticket_merge_search, name='ticket_merge_search'),
    path('tickets/<int:pk>/merge/', views.ticket_merge, name='ticket_merge'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.export_tickets_csv, name='export_csv'),
    path('settings/', views.settings_view, name='settings'),
    path('dev/email-preview/', views.email_preview, name='email_preview'),
    path('settings/import-sysaid/', views.import_sysaid, name='import_sysaid'),
    path('settings/categories/api/', views.categories_api, name='categories_api'),
    # ── Employee portal ───────────────────────────────────────────────────────
    path('portal/teams-sso/', views.portal_teams_sso, name='portal_teams_sso'),
    path('portal/teams-entry/', views.portal_teams_entry, name='portal_teams_entry'),
    path('portal/', views.portal_dashboard, name='portal_dashboard'),
    path('portal/new/', views.portal_ticket_create, name='portal_ticket_create'),
    path('portal/tickets/<int:pk>/', views.portal_ticket_detail, name='portal_ticket_detail'),
    path('portal/preview/enter/', views.portal_preview_enter, name='portal_preview_enter'),
    path('portal/preview/exit/', views.portal_preview_exit, name='portal_preview_exit'),
]
