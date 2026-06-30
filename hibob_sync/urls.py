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
    path('hibob-sync/provisioning/<int:req_id>/log/', views.provisioning_log, name='hibob_sync_provisioning_log'),
    path('hibob-sync/api/provisioning/statuses/', views.api_provisioning_statuses, name='hibob_sync_api_provisioning_statuses'),
    # Agent-facing API — sync triggers
    path('hibob-sync/api/pending/', views.api_pending, name='hibob_sync_api_pending'),
    path('hibob-sync/api/claim/<int:trigger_id>/', views.api_claim, name='hibob_sync_api_claim'),
    path('hibob-sync/api/report/', views.api_report, name='hibob_sync_api_report'),
    # Agent-facing API — provisioning
    path('hibob-sync/api/provisioning/pending/', views.api_provisioning_pending, name='hibob_sync_api_provisioning_pending'),
    path('hibob-sync/api/provisioning/<int:req_id>/data/', views.api_provisioning_data, name='hibob_sync_api_provisioning_data'),
    path('hibob-sync/api/provisioning/claim/<int:req_id>/', views.api_provisioning_claim, name='hibob_sync_api_provisioning_claim'),
    path('hibob-sync/api/provisioning/report/', views.api_provisioning_report, name='hibob_sync_api_provisioning_report'),
    path('hibob-sync/offboarding/toggle/', views.hibob_sync_offboarding_toggle, name='hibob_sync_offboarding_toggle'),
    path('hibob-sync/offboarding/trigger/', views.offboarding_manual_trigger, name='hibob_sync_offboarding_trigger'),
    # Agent-facing API — offboarding
    path('hibob-sync/api/offboarding/pending/', views.api_offboarding_pending, name='hibob_sync_api_offboarding_pending'),
    path('hibob-sync/api/offboarding/claim/<int:req_id>/', views.api_offboarding_claim, name='hibob_sync_api_offboarding_claim'),
    path('hibob-sync/api/offboarding/report/', views.api_offboarding_report, name='hibob_sync_api_offboarding_report'),
    # Offboarding UI actions
    path('hibob-sync/offboarding/<int:req_id>/cancel/', views.offboarding_cancel, name='hibob_sync_offboarding_cancel'),
    path('hibob-sync/offboarding/<int:req_id>/log/', views.offboarding_log, name='hibob_sync_offboarding_log'),
    path('hibob-sync/api/offboarding/statuses/', views.api_offboarding_statuses, name='hibob_sync_api_offboarding_statuses'),
    path('hibob-sync/offboarding/preview-manager-email/', views.offboarding_manager_email_preview, name='hibob_sync_offboarding_preview_manager_email'),
    # Credentials sharing
    path('hibob-sync/api/provisioning/<int:req_id>/store-credentials/', views.api_store_credentials, name='hibob_sync_api_store_credentials'),
    path('hibob-sync/credentials/<int:req_id>/', views.provisioning_credentials, name='provisioning_credentials'),
    path('hibob-sync/credentials/<int:req_id>/viewed/', views.provisioning_credentials_viewed, name='provisioning_credentials_viewed'),
    path('hibob-sync/test-credentials-email/', views.test_credentials_email, name='hibob_sync_test_credentials_email'),
]
