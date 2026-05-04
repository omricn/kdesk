from django.urls import path
from . import views

urlpatterns = [
    # Admin
    path('kb/', views.kb_list, name='kb_list'),
    path('kb/create/', views.kb_create, name='kb_create'),
    path('kb/<int:pk>/edit/', views.kb_edit, name='kb_edit'),
    path('kb/<int:pk>/delete/', views.kb_delete, name='kb_delete'),
    path('kb/<int:pk>/publish/', views.kb_publish, name='kb_publish'),
    path('kb/api/items/', views.kb_items_api, name='kb_items_api'),
    path('kb/attachments/<int:pk>/download/', views.kb_download_attachment, name='kb_download_attachment'),

    # Portal
    path('portal/kb/', views.portal_kb, name='portal_kb'),
    path('portal/kb/search/', views.portal_kb_search, name='portal_kb_search'),
    path('portal/kb/article/<int:pk>/', views.portal_kb_article, name='portal_kb_article'),
    path('portal/kb/<int:subcategory_pk>/', views.portal_kb_subcategory, name='portal_kb_subcategory'),
    path('portal/kb/<int:subcategory_pk>/uncategorized/', views.portal_kb_uncategorized, name='portal_kb_uncategorized'),
    path('portal/kb/<int:subcategory_pk>/<int:item_pk>/', views.portal_kb_item, name='portal_kb_item'),
]
