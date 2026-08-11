import http.server
import json
import socketserver
import threading
import time
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.estoque.models import Endereco, Produto
from apps.recebimento import whatsapp as whatsapp_module
from apps.recebimento import zpl as zpl_module
from apps.recebimento.models import Palete
from apps.recebimento.services import LoteVazioError, aprovar_lote


class MontarZplTests(TestCase):

    @mock.patch('apps.recebimento.zpl.timezone.now')
    def test_zpl_contem_sku_lote_peso_data_hora_e_barcode(self, mock_now):
        mock_now.return_value = timezone.make_aware(datetime(2026, 8, 10, 14, 30))
        palete = Palete(barcode_palete='PLT-TESTE', sku='CAFE-500G', lote='L001', peso_kg=Decimal('20.500'))
        conteudo = zpl_module.montar_zpl_etiqueta(palete)
        self.assertIn('CAFE-500G', conteudo)
        self.assertIn('L001', conteudo)
        self.assertIn('20.500', conteudo)
        self.assertIn('PLT-TESTE', conteudo)
        self.assertIn('10/08/2026 14:30', conteudo)
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


class MontarMensagemLotesTests(TestCase):

    def test_mensagem_contem_todos_os_lotes_skus_quantidades_e_barcodes(self):
        paletes_x = [
            Palete(barcode_palete='PLT-A1', sku='CAFE-500G', descricao='Café 500g', lote='LT-X', peso_kg=Decimal('20.000')),
            Palete(barcode_palete='PLT-A2', sku='CAFE-500G', descricao='Café 500g', lote='LT-X', peso_kg=Decimal('20.000')),
        ]
        paletes_y = [
            Palete(barcode_palete='PLT-B1', sku='CAFE-1KG', descricao='Café 1kg', lote='LT-Y', peso_kg=Decimal('20.000')),
        ]
        mensagem = whatsapp_module.montar_mensagem_lotes([('LT-X', paletes_x), ('LT-Y', paletes_y)])
        self.assertIn('LT-X', mensagem)
        self.assertIn('LT-Y', mensagem)
        self.assertIn('CAFE-500G', mensagem)
        self.assertIn('CAFE-1KG', mensagem)
        self.assertIn('PLT-A1', mensagem)
        self.assertIn('PLT-A2', mensagem)
        self.assertIn('PLT-B1', mensagem)
        self.assertIn('3', mensagem)  # total de paletes
        self.assertNotIn('destino', mensagem.lower())  # área ainda não é escolhida nesta etapa

    def test_mensagem_com_um_unico_lote_aprovado(self):
        paletes = [
            Palete(barcode_palete='PLT-C1', sku='CAFE-500G', descricao='Café 500g', lote='LT-Z', peso_kg=Decimal('20.000')),
        ]
        mensagem = whatsapp_module.montar_mensagem_lotes([('LT-Z', paletes)])
        self.assertIn('LT-Z', mensagem)
        self.assertIn('PLT-C1', mensagem)


class EnviarWhatsappHttpTests(TestCase):

    def test_envia_post_json_com_client_token_para_a_url_informada(self):
        recebido = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                tamanho = int(self.headers['Content-Length'])
                recebido['corpo'] = json.loads(self.rfile.read(tamanho))
                recebido['client_token'] = self.headers.get('Client-Token')
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        host, port = server.server_address
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            whatsapp_module.enviar_whatsapp(
                'Mensagem de teste', numero='5511888888888',
                url=f'http://{host}:{port}/send-text', timeout=2,
            )
            thread.join(timeout=2)
        finally:
            server.server_close()

        self.assertEqual(recebido['corpo'], {'phone': '5511888888888', 'message': 'Mensagem de teste'})
        self.assertEqual(recebido['client_token'], settings.ZAPI_CLIENT_TOKEN)

    def test_falha_de_conexao_nao_propaga_excecao(self):
        # Porta sem listener: deve logar e retornar, nunca levantar.
        whatsapp_module.enviar_whatsapp('Mensagem', numero='5511888888888', url='http://127.0.0.1:1/send-text', timeout=1)


class RecebimentoViewTests(TestCase):
    """Envio do colaborador: seleciona o produto do catálogo, dispara a impressão
    imediatamente (se peso válido) e fica aguardando bipagem de transferência."""

    def setUp(self):
        self.user = User.objects.create_user('operador1', password='senha-teste-123')
        self.client.login(username='operador1', password='senha-teste-123')
        self.produto = Produto.objects.create(sku='CAFE-500G', descricao='Café 500g')

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_submissao_valida_imprime_e_fica_aguardando_bipagem_de_transferencia(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-2026-001',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(sku='CAFE-500G')
        self.assertEqual(palete.sku, 'CAFE-500G')
        self.assertEqual(palete.descricao, 'Café 500g')
        self.assertEqual(palete.lote, 'LOTE-2026-001')
        self.assertEqual(palete.peso_kg, Decimal('500'))
        self.assertEqual(palete.estado, 'CRIADO')
        self.assertTrue(palete.barcode_palete)
        self.assertIsNone(palete.area_destino)
        mock_disparar.assert_called_once_with(palete)

    def test_lote_e_obrigatorio(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'lote', 'This field is required.')
        self.assertEqual(Palete.objects.count(), 0)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_peso_kg_enviado_no_post_e_ignorado_permanece_fixo_em_500(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-2026-002',
            'peso_kg': '999.999',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LOTE-2026-002')
        self.assertEqual(palete.peso_kg, Decimal('500'))

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_lote_e_normalizado_com_strip_e_maiusculas(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': '  lote-abc  ',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LOTE-ABC')
        self.assertEqual(palete.lote, 'LOTE-ABC')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('recebimento:novo'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:novo')}")

    def test_produto_inativo_nao_aparece_no_formulario(self):
        Produto.objects.create(sku='CAFE-DESCONTINUADO', descricao='Café descontinuado', ativo=False)
        response = self.client.get(reverse('recebimento:novo'))
        self.assertNotContains(response, 'CAFE-DESCONTINUADO')

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_lotes_digitados_diferentes_geram_paletes_com_lotes_diferentes(self, mock_disparar):
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-A'})
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-B'})

        paletes = list(Palete.objects.filter(sku='CAFE-500G').order_by('criado_em'))
        self.assertEqual(len(paletes), 2)
        self.assertNotEqual(paletes[0].lote, paletes[1].lote)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_duas_submissoes_com_mesmo_lote_digitado_compartilham_lote_mas_nao_barcode(self, mock_disparar):
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-COMPARTILHADO'})
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-COMPARTILHADO'})

        paletes = list(Palete.objects.filter(sku='CAFE-500G').order_by('criado_em'))
        self.assertEqual(len(paletes), 2)
        self.assertEqual(paletes[0].lote, paletes[1].lote)
        self.assertNotEqual(paletes[0].barcode_palete, paletes[1].barcode_palete)


class TransferenciaGestorTests(TestCase):
    """Gestor bipa paletes impressos para a fila de transferência e aprova lotes inteiros."""

    def setUp(self):
        self.gestor = User.objects.create_user('gestor3', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

        self.operador = User.objects.create_user('operador3', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operadores'))

        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='L-CONF', descricao='Café 500g', peso_kg='20.500', estado='CRIADO',
        )

    def test_gestor_bipa_palete_criado_muda_para_em_transferencia(self):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')

    def test_bipar_palete_ja_em_transferencia_mostra_erro(self):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_palete_conferido_mostra_erro(self):
        self.palete.estado = 'CONFERIDO'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_barcode_inexistente_mostra_erro(self):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': 'PLT-NAO-EXISTE',
        }, follow=True)
        self.assertContains(response, 'não encontrado')

    def test_bipar_barcode_vazio_mostra_erro(self):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': '',
        }, follow=True)
        self.assertContains(response, 'Bipe ou digite')

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_gestor_aprova_lote_muda_todos_para_conferido_sem_area_e_notifica(self, mock_notificar):
        outro_palete = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='EM_TRANSFERENCIA',
        )
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'aprovar_pendentes': '1',
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(outro_palete.estado, 'CONFERIDO')
        self.assertIsNone(self.palete.area_destino)
        self.assertIsNone(outro_palete.area_destino)

        mock_notificar.assert_called_once()
        aprovados = mock_notificar.call_args.args[0]
        self.assertEqual(len(aprovados), 1)
        self.assertEqual(aprovados[0][0], self.palete.lote)
        self.assertCountEqual([p.pk for p in aprovados[0][1]], [self.palete.pk, outro_palete.pk])

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_pendentes_com_dois_lotes_diferentes_aprova_ambos_em_um_clique_e_notifica_uma_vez(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        outro_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-OUTRO', descricao='Café 1kg', peso_kg='15.000', estado='EM_TRANSFERENCIA',
        )

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_pendentes': '1'})
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(outro_palete.estado, 'CONFERIDO')

        mock_notificar.assert_called_once()
        aprovados = mock_notificar.call_args.args[0]
        lotes_notificados = {lote for lote, _ in aprovados}
        self.assertEqual(lotes_notificados, {self.palete.lote, 'L-OUTRO'})

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    @mock.patch('apps.recebimento.services.aprovar_lote')
    def test_aprovar_pendentes_lote_esvaziado_por_corrida_e_pulado_mas_outros_sao_aprovados_e_notificados(
        self, mock_aprovar_lote, mock_notificar,
    ):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        outro_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-OUTRO', descricao='Café 1kg', peso_kg='15.000', estado='EM_TRANSFERENCIA',
        )

        def aprovar_com_corrida_simulada(lote):
            if lote == 'L-OUTRO':
                # Simula outro Gestor aprovando 'L-OUTRO' no instante exato entre a
                # consulta de lotes pendentes e o lock deste lote específico dentro
                # de aprovar_lotes_pendentes — só assim o lote chega vazio ao lock.
                aprovar_lote(lote)
            return aprovar_lote(lote)

        mock_aprovar_lote.side_effect = aprovar_com_corrida_simulada

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_pendentes': '1'}, follow=True,
        )

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')

        mock_notificar.assert_called_once()
        aprovados = mock_notificar.call_args.args[0]
        self.assertEqual([lote for lote, _ in aprovados], [self.palete.lote])

        self.assertContains(response, 'Nenhum palete pendente')
        self.assertContains(response, self.palete.lote)

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_pendentes_sem_nenhum_lote_pendente_mostra_erro_sem_notificar(self, mock_notificar):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_pendentes': '1'}, follow=True,
        )
        self.assertContains(response, 'Nenhum lote pendente')
        mock_notificar.assert_not_called()

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_novo_palete_do_mesmo_lote_apos_aprovacao_forma_novo_grupo_pendente(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_pendentes': '1'})
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')

        novo_palete = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='19.000', estado='EM_TRANSFERENCIA',
        )
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        grupos = response.context['grupos']
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['lote'], self.palete.lote)
        self.assertEqual([p.pk for p in grupos[0]['paletes']], [novo_palete.pk])

    def test_operador_recebe_403(self):
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:transferencia_gestor')}")


class AprovarLoteConcorrenciaTests(TransactionTestCase):
    """Simula Gestores concorrentes tentando aprovar o mesmo lote simultaneamente."""

    def setUp(self):
        self.lote = 'LT-CONCORRENCIA'
        self.paletes = [
            Palete.objects.create(
                sku='CAFE-500G', lote=self.lote, descricao='Café 500g', peso_kg='20.000', estado='EM_TRANSFERENCIA',
            )
            for _ in range(3)
        ]

    def test_apenas_um_vencedor_aprova_o_lote(self):
        NUM_CONCORRENTES = 5
        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)

        def tentar_aprovar():
            barreira.wait()
            # Mesma técnica de retry-on-lock de AlocarPaleteConcorrenciaTests (apps/estoque/tests.py):
            # SQLite não bloqueia-e-espera como Postgres/MySQL, então retenta para simular
            # a espera real de produção e observar sempre a regra de negócio (LoteVazioError).
            try:
                for _ in range(100):
                    try:
                        aprovar_lote(self.lote)
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except LoteVazioError:
                resultados.append('vazio')
            finally:
                connection.close()

        threads = [threading.Thread(target=tentar_aprovar) for _ in range(NUM_CONCORRENTES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(resultados.count('sucesso'), 1)
        self.assertEqual(resultados.count('vazio'), NUM_CONCORRENTES - 1)
        self.assertEqual(Palete.objects.filter(lote=self.lote, estado='CONFERIDO').count(), 3)


class FluxoCompletoOperadorGestorTests(TestCase):
    """Integra o fluxo real de ponta a ponta com os dois papéis:
    Operador envia (imprime na hora) -> Gestor bipa para transferência -> Gestor aprova
    o lote (notifica a logística) -> Operador guarda -> Gestor finaliza.
    Também verifica que cada papel é barrado nas telas exclusivas do outro.
    """

    def setUp(self):
        self.operador = User.objects.create_user('operador_fluxo', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operadores'))

        self.gestor = User.objects.create_user('gestor_fluxo', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestores'))

        self.endereco = Endereco.objects.create(
            guarda=9, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=9),
            capacidade=21, ocupacao_atual=0,
        )
        self.produto = Produto.objects.create(sku='CAFE-FLUXO', descricao='Café fluxo completo')

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_fluxo_completo_operador_e_gestor(self, mock_disparar_impressao, mock_disparar_notificacao):
        # 1) Operador envia os dados do palete — etiqueta impressa na hora, fica CRIADO.
        self.client.login(username='operador_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk, 'lote': 'LOTE-FLUXO-001',
        })
        self.assertEqual(response.status_code, 302)
        palete = Palete.objects.get(sku='CAFE-FLUXO')
        self.assertEqual(palete.estado, 'CRIADO')
        self.assertEqual(palete.peso_kg, Decimal('500'))
        mock_disparar_impressao.assert_called_once_with(palete)

        # 2) Operador não pode acessar a transferência do Gestor.
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # 3) Gestor bipa o palete para a fila de transferência...
        self.client.login(username='gestor_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': palete.barcode_palete,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'EM_TRANSFERENCIA')

        # ...e então aprova todos os lotes pendentes num único clique, notificando a logística.
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_pendentes': '1'})
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'CONFERIDO')
        self.assertIsNone(palete.area_destino)
        mock_disparar_notificacao.assert_called_once()
        self.client.logout()

        # 4) Operador bipa o palete aprovado (Etapa A) e vê os dados do produto.
        self.client.login(username='operador_fluxo', password='senha-teste-123')
        etapa_a = self.client.post(reverse('estoque:putaway'), {'barcode_palete': palete.barcode_palete})
        self.assertRedirects(etapa_a, f"{reverse('estoque:putaway')}?barcode={palete.barcode_palete}")

        tela_produto = self.client.get(reverse('estoque:putaway'), {'barcode': palete.barcode_palete})
        self.assertContains(tela_produto, 'CAFE-FLUXO')
        self.assertContains(tela_produto, self.endereco.codigo)

        # Depois bipa o endereço (Etapa B) e confirma a guarda.
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete.barcode_palete, 'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(palete.estado, 'ARMAZENADO')
        self.assertEqual(palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

        # 5) Operador não pode confirmar a guarda final — isso é exclusivo do Gestor.
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # 6) Gestor confirma com senha e finaliza o palete (sincronizado com o SAP).
        self.client.login(username='gestor_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-teste-123'})
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'FINALIZADO')
