from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('relatorios/divergencias/', views.relatorio_divergencias, name='relatorio_divergencias'),
]
