from django.urls import path

from . import views, views_notificacoes

urlpatterns = [
    path('', views.home, name='home'),
    path('estatisticas/', views.estatisticas, name='estatisticas'),
    path('usuarios/', views.usuarios, name='usuarios'),
    path('usuarios/buscar/', views.buscar_usuarios_ad_json, name='buscar_usuarios_ad'),
    path('notificacoes/', views_notificacoes.notificacoes, name='notificacoes'),
    path('notificacoes/json/', views_notificacoes.notificacoes_json, name='notificacoes_json'),
    path('notificacoes/<int:pk>/ler/', views_notificacoes.notificacao_ler, name='notificacao_ler'),
    path('notificacoes/ler-todas/', views_notificacoes.notificacoes_ler_todas, name='notificacoes_ler_todas'),
]
