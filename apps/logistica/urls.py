from django.urls import path

from . import views

app_name = 'logistica'

urlpatterns = [
    path('putaway/', views.putaway, name='putaway'),
    path('mapa/', views.mapa_estoque, name='mapa_estoque'),
    path('mapa/dados/', views.mapa_estoque_dados, name='mapa_estoque_dados'),
    path('consultar/', views.consultar_palete, name='consultar_palete'),
    path('inspecionar/<int:pk>/', views.inspecionar_palete, name='inspecionar_palete'),
    path('etiquetas-corredor/', views.etiquetas_corredor, name='etiquetas_corredor'),
    path('etiquetas-corredor/preview/<str:corredor>/', views.etiqueta_corredor_preview, name='etiqueta_corredor_preview'),
]
