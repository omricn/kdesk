from django.urls import path
from . import views

urlpatterns = [
    path('hibob-sync/', views.hibob_sync_dashboard, name='hibob_sync_dashboard'),
    path('hibob-sync/trigger/', views.hibob_sync_trigger, name='hibob_sync_trigger'),
    path('hibob-sync/cancel/<int:trigger_id>/', views.hibob_sync_cancel, name='hibob_sync_cancel'),
    path('hibob-sync/log/<int:run_id>/', views.hibob_sync_log, name='hibob_sync_log'),
    path('hibob-sync/provisioning/toggle/', views.hibob_sync_provisioning_toggle, name='hibob_sync_provisioning_toggle'),
    path('hibob-sync/provisioning/<int:req_id>/requeue/', views.provisioning_requeue, name='hibob_sync_provisioning_requeue'),
    path('hibob-sync/provisioning/<int:req_id>/cancel/', views.provisioning_cancel, name='hibob_sync_provisioning_cancel'),
    path('hibob-sync/provisioning/<int:req_id>/pause/', views.provisioning_pause, name='hibob_sync_provisioning_pause'),
    path('hibob-sync/provisioning/<int:req_id>/resume/', views.provisioning_resume, name='hibob_sync_provisioning_resume'),
    # Agent-facing API — sync triggers
    path('hibob-sync/api/pending/', views.api_pending, name='hibob_sync_api_pending'),
    path('hibob-sync/api/claim/<int:trigger_id>/', views.api_claim, name='hibob_sync_api_claim'),
    path('hibob-sync/api/report/', views.api_report, name='hibob_sync_api_report'),
    # Agent-facing API — provisioning
    path('hibob-sync/api/provisioning/pending/', views.api_provisioning_pending, name='hibob_sync_api_provisioning_pending'),
    path('hibob-sync/api/provisioning/<int:req_id>/data/', views.api_provisioning_data, name='hibob_sync_api_provisioning_data'),
    path('hibob-sync/api/provisioning/claim/<int:req_id>/', views.api_provisioning_claim, name='hibob_sync_api_provisioning_claim'),
    path('hibob-sync/api/provisioning/report/', views.api_provisioning_report, name='hibob_sync_api_provisioning_report'),
]
