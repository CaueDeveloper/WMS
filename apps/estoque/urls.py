from django.urls import path

from . import views

app_name = 'estoque'

urlpatterns = [
    path('putaway/', views.putaway, name='putaway'),
    path('plano-armazenagem/', views.plano_armazenagem, name='plano_armazenagem'),
    path('mapa/', views.mapa_estoque, name='mapa_estoque'),
    path('mapa/dados/', views.mapa_estoque_dados, name='mapa_estoque_dados'),
    path('consultar/', views.consultar_palete, name='consultar_palete'),
    path('inspecionar/<int:pk>/', views.inspecionar_palete, name='inspecionar_palete'),
]
