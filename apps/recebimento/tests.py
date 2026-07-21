import socketserver
import threading
import time
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.estoque.models import AreaDestino, Endereco, Produto
from apps.recebimento import zpl as zpl_module
from apps.recebimento.models import Palete, gerar_proximo_lote


class MontarZplTests(TestCase):

    def test_zpl_contem_sku_lote_peso_e_barcode(self):
        palete = Palete(barcode_palete='PLT-TESTE', sku='CAFE-500G', lote='L001', peso_kg=Decimal('20.500'))
        conteudo = zpl_module.montar_zpl_etiqueta(palete)
        self.assertIn('CAFE-500G', conteudo)
        self.assertIn('L001', conteudo)
        self.assertIn('20.500', conteudo)
        self.assertIn('PLT-TESTE', conteudo)
        self.assertTrue(conteudo.startswith('^XA'))
        self.assertTrue(conteudo.strip().endswith('^XZ'))


class EnviarZplSocketTests(TestCase):

    def test_envia_bytes_para_socket_tcp(self):
        recebido = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                recebido.append(self.request.recv(4096))

        with socketserver.TCPServer(('127.0.0.1', 0), Handler) as server:
            host, port = server.server_address
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            zpl_module.enviar_zpl('^XA^FDTESTE^XZ', host=host, port=port, timeout=2)
            thread.join(timeout=2)

        self.assertEqual(recebido, [b'^XA^FDTESTE^XZ'])

    def test_falha_de_conexao_nao_propaga_excecao(self):
        # Porta sem listener: deve logar e retornar, nunca levantar.
        zpl_module.enviar_zpl('^XA^XZ', host='127.0.0.1', port=1, timeout=1)


class GerarProximoLoteTests(TestCase):

    def test_gera_lotes_sequenciais_comecando_em_lt00001(self):
        self.assertEqual(gerar_proximo_lote(), 'LT00001')
        self.assertEqual(gerar_proximo_lote(), 'LT00002')
        self.assertEqual(gerar_proximo_lote(), 'LT00003')


class GerarProximoLoteConcorrenciaTests(TransactionTestCase):
    """Garante que chamadas concorrentes nunca gerem o mesmo número de lote."""

    def test_lotes_gerados_concorrentemente_sao_todos_unicos(self):
        NUM_CONCORRENTES = 5
        lotes = []
        barreira = threading.Barrier(NUM_CONCORRENTES)

        def gerar():
            barreira.wait()
            # Mesmo retry-com-backoff usado em AlocarPaleteConcorrenciaTests: SQLite não
            # bloqueia-e-espera por linha, então sob disputa simula-se a espera que
            # Postgres/MySQL (produção) fariam nativamente.
            for _ in range(100):
                try:
                    lotes.append(gerar_proximo_lote())
                    return
                except OperationalError:
                    time.sleep(0.05)
            raise AssertionError('lock persistente ao gerar lote')

        threads = [threading.Thread(target=lambda: (gerar(), connection.close())) for _ in range(NUM_CONCORRENTES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(lotes), NUM_CONCORRENTES)
        self.assertEqual(len(set(lotes)), NUM_CONCORRENTES)


class RecebimentoViewTests(TestCase):
    """Envio do colaborador: seleciona o produto do catálogo — não imprime nem confere sozinho."""

    def setUp(self):
        self.user = User.objects.create_user('operador1', password='senha-teste-123')
        self.client.login(username='operador1', password='senha-teste-123')
        self.produto = Produto.objects.create(sku='CAFE-500G', descricao='Café 500g')

    def test_submissao_valida_fica_aguardando_conferencia_do_gestor(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'peso_kg': '20.500',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LT00001')
        self.assertEqual(palete.sku, 'CAFE-500G')
        self.assertEqual(palete.descricao, 'Café 500g')
        self.assertEqual(palete.estado, 'CRIADO')
        self.assertTrue(palete.barcode_palete)
        self.assertIsNone(palete.area_destino)

    def test_peso_invalido_marca_divergente(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'peso_kg': '0',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LT00001')
        self.assertEqual(palete.estado, 'DIVERGENTE')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('recebimento:novo'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:novo')}")

    def test_produto_inativo_nao_aparece_no_formulario(self):
        Produto.objects.create(sku='CAFE-DESCONTINUADO', descricao='Café descontinuado', ativo=False)
        response = self.client.get(reverse('recebimento:novo'))
        self.assertNotContains(response, 'CAFE-DESCONTINUADO')

    def test_cada_submissao_gera_barcode_e_lote_unicos_e_incrementais(self):
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'peso_kg': '18.000'})
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'peso_kg': '18.000'})

        paletes = list(Palete.objects.filter(sku='CAFE-500G').order_by('lote'))
        self.assertEqual([p.lote for p in paletes], ['LT00001', 'LT00002'])
        self.assertEqual(len({p.barcode_palete for p in paletes}), 2)


class ConferenciaGestorTests(TestCase):

    def setUp(self):
        self.gestor = User.objects.create_user('gestor3', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

        self.operador = User.objects.create_user('operador3', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operadores'))

        self.area = AreaDestino.objects.create(nome='Área de Separação')
        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='L-CONF', descricao='Café 500g', peso_kg='20.500', estado='CRIADO',
        )

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_gestor_aprova_muda_para_conferido_define_area_e_dispara_impressao(self, mock_disparar):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:conferencia_gestor'), {
            'palete_id': self.palete.pk,
            'acao': 'aprovar',
            'area_destino': self.area.pk,
        })
        self.assertRedirects(response, reverse('recebimento:conferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(self.palete.area_destino, self.area)
        mock_disparar.assert_called_once_with(self.palete)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_gestor_rejeita_marca_divergente_sem_imprimir(self, mock_disparar):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:conferencia_gestor'), {
            'palete_id': self.palete.pk,
            'acao': 'rejeitar',
        })
        self.assertRedirects(response, reverse('recebimento:conferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'DIVERGENTE')
        mock_disparar.assert_not_called()

    def test_operador_recebe_403(self):
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:conferencia_gestor'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        response = self.client.get(reverse('recebimento:conferencia_gestor'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:conferencia_gestor')}")


class FluxoCompletoOperadorGestorTests(TestCase):
    """Integra o fluxo real de ponta a ponta com os dois papéis:
    Operador envia -> Gestor aprova -> Operador guarda -> Gestor finaliza.
    Também verifica que cada papel é barrado nas telas exclusivas do outro.
    """

    def setUp(self):
        self.operador = User.objects.create_user('operador_fluxo', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operadores'))

        self.gestor = User.objects.create_user('gestor_fluxo', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

        self.area = AreaDestino.objects.create(nome='Área de Separação')
        self.endereco = Endereco.objects.create(corredor=9, andar='A', codigo='09-A', capacidade=21, ocupacao_atual=0)
        self.produto = Produto.objects.create(sku='CAFE-FLUXO', descricao='Café fluxo completo')

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_fluxo_completo_operador_e_gestor(self, mock_disparar):
        # 1) Operador envia os dados do palete — fica CRIADO, aguardando o Gestor.
        self.client.login(username='operador_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk, 'peso_kg': '15.000',
        })
        self.assertEqual(response.status_code, 302)
        palete = Palete.objects.get(sku='CAFE-FLUXO')
        self.assertEqual(palete.estado, 'CRIADO')

        # 2) Operador não pode acessar a conferência do Gestor.
        response = self.client.get(reverse('recebimento:conferencia_gestor'))
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # 3) Gestor aprova o palete, escolhe a área de destino e dispara a impressão.
        self.client.login(username='gestor_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:conferencia_gestor'), {
            'palete_id': palete.pk, 'acao': 'aprovar', 'area_destino': self.area.pk,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'CONFERIDO')
        self.assertEqual(palete.area_destino, self.area)
        mock_disparar.assert_called_once_with(palete)
        self.client.logout()

        # 4) Operador vê o palete aprovado na lista e escolhe o endereço.
        self.client.login(username='operador_fluxo', password='senha-teste-123')
        lista = self.client.get(reverse('estoque:putaway'))
        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, 'CAFE-FLUXO')
        self.assertContains(lista, '09-A')

        response = self.client.post(reverse('estoque:putaway'), {
            'palete_id': palete.pk, 'codigo_endereco': '09-A',
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(palete.estado, 'ARMAZENADO')
        self.assertEqual(palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

        # 5) Operador não pode confirmar a guarda final — isso é exclusivo do Gestor.
        response = self.client.get(reverse('relatorio_divergencias'))
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # 6) Gestor confirma com senha e finaliza o palete (sincronizado com o SAP).
        self.client.login(username='gestor_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('relatorio_divergencias'), {'senha': 'senha-teste-123'})
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'FINALIZADO')
