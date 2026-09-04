from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('armazenagem/confirmar/', views.confirmar_armazenagem, name='confirmar_armazenagem'),
    path('estatisticas/', views.estatisticas, name='estatisticas'),
]
