from django.urls import path

from . import views

app_name = 'romaneio'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('novo/', views.novo, name='novo'),
    path('nota/<int:docentry>/', views.nota_itens, name='nota_itens'),
    path('<int:pk>/', views.detalhe, name='detalhe'),
    path('<int:pk>/coletar/', views.coletar, name='coletar'),
    path('<int:pk>/imprimir/', views.imprimir, name='imprimir'),
]
