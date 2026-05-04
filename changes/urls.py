from django.urls import path
from . import views

urlpatterns = [
    path('changes/',              views.change_list,       name='change_list'),
    path('changes/new/',          views.change_create,     name='change_create'),
    path('changes/<int:pk>/',     views.change_detail,     name='change_detail'),
    path('changes/<int:pk>/edit/', views.change_edit,      name='change_edit'),
    path('changes/<int:pk>/transition/', views.change_transition, name='change_transition'),
    path('changes/attachments/<int:pk>/download/', views.change_download_attachment, name='change_download_attachment'),
]
