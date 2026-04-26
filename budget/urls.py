from django.urls import path
from . import views

urlpatterns = [
    path('budget/', views.budget_view, name='budget'),
    path('budget/excel-proxy/', views.budget_excel_proxy, name='budget_excel_proxy'),
]
