from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('estatisticas/', views.estatisticas, name='estatisticas'),
    path('usuarios/', views.usuarios, name='usuarios'),
    path('usuarios/buscar/', views.buscar_usuarios_ad_json, name='buscar_usuarios_ad'),
]
