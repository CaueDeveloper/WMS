from django.urls import path

from . import views

app_name = 'recebimento'

urlpatterns = [
    path('receber/', views.receber_palete, name='novo'),
    path('transferencia/', views.transferencia_gestor, name='transferencia_gestor'),
]
