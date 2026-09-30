from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('estatisticas/', views.estatisticas, name='estatisticas'),
    path('usuarios/atribuir-ad/', views.atribuir_usuario_ad, name='atribuir_usuario_ad'),
    path('usuarios/atribuir-ad/buscar/', views.buscar_usuarios_ad_json, name='buscar_usuarios_ad'),
    path('usuarios/ativar/', views.ativar_usuarios, name='ativar_usuarios'),
]
