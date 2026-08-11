from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.estoque.models import Endereco
from apps.recebimento.models import Palete


class GruposPadraoTests(TestCase):

    def test_grupos_criados_pela_migration(self):
        nomes = set(Group.objects.values_list('name', flat=True))
        self.assertTrue({'Administradores', 'Gestores', 'Operadores'}.issubset(nomes))


class CriarUsuariosDemoTests(TestCase):

    def test_cria_gestor_e_operador_nos_grupos_corretos(self):
        call_command('criar_usuarios_demo')

        gestor = User.objects.get(username='gestor')
        operador = User.objects.get(username='operador')

        self.assertEqual(list(gestor.groups.values_list('name', flat=True)), ['Gestores'])
        self.assertEqual(list(operador.groups.values_list('name', flat=True)), ['Operadores'])
        self.assertTrue(gestor.check_password('Gestor@123'))
        self.assertTrue(operador.check_password('Operador@123'))

    def test_comando_e_idempotente(self):
        call_command('criar_usuarios_demo')
        call_command('criar_usuarios_demo')
        self.assertEqual(User.objects.filter(username__in=['gestor', 'operador']).count(), 2)


class AutenticacaoEPermissoesTests(TestCase):

    def setUp(self):
        self.operador = User.objects.create_user('operador1', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operadores'))

        self.gestor = User.objects.create_user('gestor1', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

    def test_login_renderiza_formulario(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_com_credenciais_validas_redireciona_para_home(self):
        response = self.client.post(reverse('login'), {
            'username': 'operador1',
            'password': 'senha-teste-123',
        })
        self.assertRedirects(response, reverse('home'))

    def test_anonimo_e_redirecionado_para_login(self):
        destino = reverse('confirmar_armazenagem')
        response = self.client.get(destino)
        self.assertRedirects(response, f"{reverse('login')}?next={destino}")

    def test_operador_recebe_403_na_confirmacao_do_gestor(self):
        self.client.login(username='operador1', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 403)

    def test_gestor_acessa_confirmacao(self):
        self.client.login(username='gestor1', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 200)


class ConfirmacaoArmazenagemTests(TestCase):

    def setUp(self):
        self.gestor = User.objects.create_user('gestor2', password='senha-gestor-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

        self.endereco = Endereco.objects.create(
            guarda=5, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=5),
            capacidade=21, ocupacao_atual=1,
        )
        self.palete = Palete.objects.create(
            sku='CAFE-X', lote='L-X', descricao='Café X', peso_kg='10.000',
            estado='ARMAZENADO', endereco=self.endereco,
        )
        self.client.login(username='gestor2', password='senha-gestor-123')

    def test_senha_correta_finaliza_paletes_armazenados(self):
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-gestor-123'})
        self.assertRedirects(response, reverse('confirmar_armazenagem'))
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'FINALIZADO')

    def test_senha_incorreta_nao_altera_estado(self):
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-errada'})
        self.assertEqual(response.status_code, 200)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'ARMAZENADO')
