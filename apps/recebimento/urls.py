from django.urls import path

from . import views

app_name = 'recebimento'

urlpatterns = [
    path('receber/', views.receber_palete, name='novo'),
    path('conferencia/', views.conferencia_gestor, name='conferencia_gestor'),
]
