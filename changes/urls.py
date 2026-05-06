from django.urls import path
from . import views

urlpatterns = [
    path('changes/',              views.change_list,       name='change_list'),
    path('changes/new/',          views.change_create,     name='change_create'),
    path('changes/<int:pk>/',     views.change_detail,     name='change_detail'),
    path('changes/<int:pk>/edit/', views.change_edit,      name='change_edit'),
    path('changes/<int:pk>/transition/', views.change_transition, name='change_transition'),
    path('changes/attachments/<int:pk>/download/', views.change_download_attachment, name='change_download_attachment'),
    path('changes/<int:pk>/ticket-search/', views.change_ticket_search, name='change_ticket_search'),
    path('changes/<int:pk>/link-ticket/', views.change_link_ticket, name='change_link_ticket'),
    path('changes/<int:pk>/unlink-ticket/', views.change_unlink_ticket, name='change_unlink_ticket'),
]
