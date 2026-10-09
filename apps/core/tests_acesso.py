from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class AcessoEstatisticasTests(TestCase):
    def test_usuario_sem_grupo_nao_acessa_estatisticas_nem_ve_movimentacoes(self):
        u = User.objects.create_user('semgrupo', password='x')
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('estatisticas'), secure=True).status_code, 403)
        self.assertEqual(list(self.client.get(reverse('home'), secure=True).context['recentes']), [])

    def test_gestor_acessa_estatisticas(self):
        u = User.objects.create_user('gest', password='x')
        u.groups.add(Group.objects.get_or_create(name='Gestor Produção')[0])
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('estatisticas'), secure=True).status_code, 200)

    def test_rotas_de_senha_do_django_nao_existem(self):
        self.assertEqual(self.client.get('/accounts/password_reset/', secure=True).status_code, 404)
