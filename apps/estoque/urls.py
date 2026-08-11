from django.urls import path

from . import views

app_name = 'estoque'

urlpatterns = [
    path('putaway/', views.putaway, name='putaway'),
    path('mapa/', views.mapa_estoque, name='mapa_estoque'),
    path('consultar/', views.consultar_palete, name='consultar_palete'),
]
