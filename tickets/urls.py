from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/new/', views.ticket_create, name='ticket_create'),
    path('tickets/bulk/', views.ticket_bulk_action, name='ticket_bulk_action'),
    path('tickets/<int:pk>/', views.ticket_detail, name='ticket_detail'),
    path('tickets/<int:pk>/categorize/', views.ticket_categorize, name='ticket_categorize'),
    path('tickets/<int:pk>/send-email/', views.ticket_send_email, name='ticket_send_email'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.export_tickets_csv, name='export_csv'),
    path('settings/', views.settings_view, name='settings'),
]
