from django.urls import path

from . import views

app_name = 'producao'

urlpatterns = [
    path('producao/', views.producao, name='novo'),
    path('receber/preview/', views.previsualizar_etiqueta, name='preview_etiqueta'),
    path('producao/estatisticas/', views.estatisticas_producao, name='estatisticas'),
    path('transferencia/', views.transferencia_gestor, name='transferencia_gestor'),
    path('transferencia-logistica/', views.transferencia_logistica, name='transferencia_logistica'),
    path('transferencia/relatorio/', views.relatorio_aprovacao_producao, name='relatorio_aprovacao_producao'),
    path(
        'transferencia-logistica/relatorio/',
        views.relatorio_aprovacao_logistica,
        name='relatorio_aprovacao_logistica',
    ),
]
