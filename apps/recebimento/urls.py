from django.urls import path

from . import views

app_name = 'recebimento'

urlpatterns = [
    path('receber/', views.receber_palete, name='novo'),
    path('transferencia/', views.transferencia_gestor, name='transferencia_gestor'),
    path('transferencia-logistica/', views.transferencia_logistica, name='transferencia_logistica'),
    path('transferencia/relatorio/', views.relatorio_aprovacao_producao, name='relatorio_aprovacao_producao'),
    path(
        'transferencia-logistica/relatorio/',
        views.relatorio_aprovacao_logistica,
        name='relatorio_aprovacao_logistica',
    ),
]
