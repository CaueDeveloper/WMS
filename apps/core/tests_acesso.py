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

    def _get(self, grupo, rota):
        u, _ = User.objects.get_or_create(username='u_' + grupo.replace(' ', '_'))
        u.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(u)
        return self.client.get(reverse(rota), secure=True).status_code

    def test_gestor_producao_acessa_telas_do_setor(self):
        self.assertEqual(self._get('Gestor Produção', 'producao:novo'), 200)
        self.assertEqual(self._get('Gestor Produção', 'producao:transferencia_gestor'), 200)

    def test_gestor_logistica_acessa_telas_do_setor(self):
        for rota in ('logistica:putaway', 'producao:transferencia_logistica'):
            self.assertEqual(self._get('Gestor Logística', rota), 200, rota)

    def test_gestor_nao_acessa_telas_de_outro_setor_nem_administracao(self):
        self.assertEqual(self._get('Gestor Produção', 'logistica:putaway'), 403)
        self.assertEqual(self._get('Gestor Logística', 'producao:novo'), 403)
        self.assertEqual(self._get('Gestor Logística', 'usuarios'), 403)

    def test_rotas_de_senha_do_django_nao_existem(self):
        self.assertEqual(self.client.get('/accounts/password_reset/', secure=True).status_code, 404)
