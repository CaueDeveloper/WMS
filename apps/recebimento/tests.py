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
from apps.recebimento.services import LoteVazioError, aprovar_lote, aprovar_lote_logistica


class MontarZplTests(TestCase):

    @mock.patch('apps.recebimento.zpl.timezone.now')
    def test_zpl_contem_lote_data_hora_barcode_e_peso(self, mock_now):
        mock_now.return_value = timezone.make_aware(datetime(2026, 8, 10, 14, 30))
        palete = Palete(
            barcode_palete='PLT-TESTE', sku='CAFE-500G', descricao='Café 500g',
            lote='L001', peso_kg=Decimal('20.500'),
        )
        conteudo = zpl_module.montar_zpl_etiqueta(palete)
        self.assertIn('L001', conteudo)
        self.assertIn('PLT-TESTE', conteudo)
        self.assertIn('10/08/2026', conteudo)
        self.assertIn('14:30', conteudo)
        self.assertIn('20.500', conteudo)
        self.assertNotIn('[[', conteudo)
        self.assertIn('^XA', conteudo)
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
        self.user.groups.add(Group.objects.get(name='Operador Produção'))
        self.client.login(username='operador1', password='senha-teste-123')
        self.produto = Produto.objects.create(sku='CAFE-500G', descricao='Café 500g')
        # Já criado pela migration de dados 0011_criar_produto_sobras.
        self.produto_sobras = Produto.objects.get(sku='SOBRAS')

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_submissao_valida_imprime_e_fica_aguardando_bipagem_de_transferencia(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-2026-001',
            'quantidade': '1',
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
        mock_disparar.assert_called_once_with([palete])

    def test_lote_e_obrigatorio(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'quantidade': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'lote', 'This field is required.')
        self.assertEqual(Palete.objects.count(), 0)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_peso_kg_enviado_no_post_e_ignorado_permanece_fixo_em_500(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-2026-002',
            'quantidade': '1',
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
            'quantidade': '1',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LOTE-ABC')
        self.assertEqual(palete.lote, 'LOTE-ABC')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('recebimento:novo'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:novo')}")

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_produto_sobras_usa_peso_digitado_em_vez_do_fixo(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto_sobras.pk,
            'lote': 'LOTE-SOBRAS-1',
            'quantidade': '1',
            'peso_kg': '37.250',
        })
        self.assertEqual(response.status_code, 302)

        palete = Palete.objects.get(lote='LOTE-SOBRAS-1')
        self.assertEqual(palete.sku, 'SOBRAS')
        self.assertEqual(palete.peso_kg, Decimal('37.250'))

    def test_produto_sobras_sem_peso_informado_mostra_erro(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto_sobras.pk,
            'lote': 'LOTE-SOBRAS-2',
            'quantidade': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'peso_kg', 'Informe o peso do palete de Sobras.')
        self.assertEqual(Palete.objects.count(), 0)

    def test_produto_sobras_com_peso_zero_mostra_erro(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto_sobras.pk,
            'lote': 'LOTE-SOBRAS-3',
            'quantidade': '1',
            'peso_kg': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_produto_inativo_nao_aparece_no_formulario(self):
        Produto.objects.create(sku='CAFE-DESCONTINUADO', descricao='Café descontinuado', ativo=False)
        response = self.client.get(reverse('recebimento:novo'))
        self.assertNotContains(response, 'CAFE-DESCONTINUADO')

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_lotes_digitados_diferentes_geram_paletes_com_lotes_diferentes(self, mock_disparar):
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-A', 'quantidade': '1'})
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-B', 'quantidade': '1'})

        paletes = list(Palete.objects.filter(sku='CAFE-500G').order_by('criado_em'))
        self.assertEqual(len(paletes), 2)
        self.assertNotEqual(paletes[0].lote, paletes[1].lote)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_duas_submissoes_com_mesmo_lote_digitado_compartilham_lote_mas_nao_barcode(self, mock_disparar):
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-COMPARTILHADO', 'quantidade': '1'})
        self.client.post(reverse('recebimento:novo'), {'produto': self.produto.pk, 'lote': 'LOTE-COMPARTILHADO', 'quantidade': '1'})

        paletes = list(Palete.objects.filter(sku='CAFE-500G').order_by('criado_em'))
        self.assertEqual(len(paletes), 2)
        self.assertEqual(paletes[0].lote, paletes[1].lote)
        self.assertNotEqual(paletes[0].barcode_palete, paletes[1].barcode_palete)

    def test_lote_ja_aprovado_nao_pode_receber_novo_palete(self):
        Palete.objects.create(
            sku='CAFE-500G', lote='LOTE-JA-APROVADO', descricao='Café 500g', peso_kg='500', estado='CONFERIDO',
        )
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-JA-APROVADO',
            'quantidade': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'lote',
            'O lote "LOTE-JA-APROVADO" já foi aprovado e não pode receber novos paletes.',
        )
        self.assertEqual(Palete.objects.filter(lote='LOTE-JA-APROVADO').count(), 1)

    def test_lote_ainda_em_producao_pode_receber_novo_palete(self):
        Palete.objects.create(
            sku='CAFE-500G', lote='LOTE-EM-PRODUCAO', descricao='Café 500g', peso_kg='500', estado='EM_TRANSFERENCIA',
        )
        with mock.patch('apps.recebimento.views.disparar_impressao_async'):
            response = self.client.post(reverse('recebimento:novo'), {
                'produto': self.produto.pk,
                'lote': 'LOTE-EM-PRODUCAO',
                'quantidade': '1',
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Palete.objects.filter(lote='LOTE-EM-PRODUCAO').count(), 2)

    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_quantidade_maior_que_um_cria_n_paletes_com_barcodes_distintos_e_mesmo_lote(self, mock_disparar):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-EM-LOTE',
            'quantidade': '5',
        })
        self.assertEqual(response.status_code, 302)

        paletes = list(Palete.objects.filter(lote='LOTE-EM-LOTE'))
        self.assertEqual(len(paletes), 5)
        self.assertEqual(len({p.barcode_palete for p in paletes}), 5)
        for palete in paletes:
            self.assertEqual(palete.sku, 'CAFE-500G')
            self.assertEqual(palete.estado, 'CRIADO')

        mock_disparar.assert_called_once()
        (chamada_paletes,), _ = mock_disparar.call_args
        self.assertEqual(len(chamada_paletes), 5)

    def test_quantidade_zero_e_rejeitada(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-QTD-0',
            'quantidade': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'quantidade',
            'Ensure this value is greater than or equal to 1.',
        )
        self.assertEqual(Palete.objects.filter(lote='LOTE-QTD-0').count(), 0)

    def test_quantidade_acima_do_maximo_e_rejeitada(self):
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk,
            'lote': 'LOTE-QTD-51',
            'quantidade': '51',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'quantidade',
            'Ensure this value is less than or equal to 50.',
        )
        self.assertEqual(Palete.objects.filter(lote='LOTE-QTD-51').count(), 0)


class TransferenciaGestorTests(TestCase):
    """Operador Produção bipa paletes impressos para a fila de transferência;
    Gestor Produção aprova lotes inteiros — papéis exclusivos e não intercambiáveis."""

    def setUp(self):
        self.operador = User.objects.create_user('operador3', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operador Produção'))

        self.gestor = User.objects.create_user('gestor3', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestor Produção'))

        self.operador_outro_setor = User.objects.create_user('operador3b', password='senha-teste-123')
        self.operador_outro_setor.groups.add(Group.objects.get(name='Operador Logística'))

        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='L-CONF', descricao='Café 500g', peso_kg='20.500', estado='CRIADO',
        )

    def test_operador_bipa_palete_criado_muda_para_em_transferencia(self):
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')

    def test_gestor_nao_consegue_bipar_palete(self):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CRIADO')

    def test_bipar_palete_ja_em_transferencia_mostra_erro(self):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_palete_conferido_mostra_erro(self):
        self.palete.estado = 'CONFERIDO'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_barcode_inexistente_mostra_erro(self):
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': 'PLT-NAO-EXISTE',
        }, follow=True)
        self.assertContains(response, 'não encontrado')

    def test_bipar_barcode_vazio_mostra_erro(self):
        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': '',
        }, follow=True)
        self.assertContains(response, 'Bipe ou digite')

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_muda_para_conferido_sem_area_e_notifica(self, mock_notificar):
        outro_palete = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='EM_TRANSFERENCIA',
        )
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'aprovar_lote': self.palete.lote,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(outro_palete.estado, 'CONFERIDO')
        self.assertIsNone(self.palete.area_destino)
        self.assertIsNone(outro_palete.area_destino)
        self.assertIsNotNone(self.palete.aprovado_producao_em)
        self.assertIsNotNone(outro_palete.aprovado_producao_em)

        mock_notificar.assert_called_once()
        (aprovados,), _ = mock_notificar.call_args
        self.assertEqual([lote for lote, _ in aprovados], [self.palete.lote])
        self.assertCountEqual([p.pk for _, paletes in aprovados for p in paletes], [self.palete.pk, outro_palete.pk])

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_operador_nao_consegue_aprovar(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='operador3', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'aprovar_lote': self.palete.lote,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')
        mock_notificar.assert_not_called()

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_incompleto_mostra_erro_sem_afetar_outro_lote_completo(self, mock_notificar):
        Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CRIADO',
        )
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        outro_lote_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-OUTRO', descricao='Café 1kg', peso_kg='15.000', estado='EM_TRANSFERENCIA',
        )

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote}, follow=True,
        )

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')
        mock_notificar.assert_not_called()
        self.assertContains(response, 'aguardando bipagem')

        # O outro lote, completo, continua aprovável independentemente do primeiro.
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': outro_lote_palete.lote},
        )
        outro_lote_palete.refresh_from_db()
        self.assertEqual(outro_lote_palete.estado, 'CONFERIDO')
        mock_notificar.assert_called_once()

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_apos_bipar_o_palete_que_faltava_funciona(self, mock_notificar):
        pendente = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CRIADO',
        )
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_TRANSFERENCIA')
        self.client.logout()

        self.client.login(username='operador3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'barcode_palete': pendente.barcode_palete})
        self.client.logout()

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote},
        )
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        pendente.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(pendente.estado, 'CONFERIDO')
        mock_notificar.assert_called_once()

    def test_lote_recem_impresso_sem_nenhuma_bipagem_ja_aparece_com_zero(self):
        # self.palete já é criado em CRIADO no setUp, sem nenhuma bipagem ainda.
        outro_palete_mesmo_lote = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CRIADO',
        )

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))

        grupos = response.context['grupos']
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['lote'], self.palete.lote)
        self.assertEqual(grupos[0]['quantidade'], 0)
        self.assertEqual(grupos[0]['pendentes_bipagem'], 2)
        self.assertEqual(grupos[0]['total_lote'], 2)
        self.assertCountEqual(
            [p.pk for p in grupos[0]['paletes']], [self.palete.pk, outro_palete_mesmo_lote.pk],
        )
        self.assertContains(response, '0/2')
        self.assertContains(response, 'contador-zerado')
        self.assertContains(response, 'selo-pendente')

    def test_grupo_pendente_mostra_contador_de_bipagem_e_desabilita_botao_do_proprio_card(self):
        Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CRIADO',
        )
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))

        grupos = response.context['grupos']
        self.assertEqual(grupos[0]['pendentes_bipagem'], 1)
        self.assertEqual(grupos[0]['total_lote'], 2)
        self.assertContains(response, '1/2')
        self.assertContains(response, 'contador-parcial')
        self.assertContains(response, 'disabled')

    def test_grupo_completo_mostra_contador_completo_e_outro_card_incompleto_nao_desabilita_o_seu(self):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        Palete.objects.create(
            sku='CAFE-1KG', lote='L-OUTRO', descricao='Café 1kg', peso_kg='15.000', estado='CRIADO',
        )

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))

        grupos = {g['lote']: g for g in response.context['grupos']}
        self.assertEqual(grupos[self.palete.lote]['pendentes_bipagem'], 0)
        self.assertEqual(grupos['L-OUTRO']['pendentes_bipagem'], 1)
        self.assertContains(response, '1/1')
        self.assertContains(response, 'contador-completo')
        # O card completo tem um botão habilitado (sem "disabled" logo após o value do seu lote).
        self.assertNotContains(
            response, f'name="aprovar_lote" value="{self.palete.lote}" disabled',
        )
        self.assertContains(
            response, f'name="aprovar_lote" value="L-OUTRO" disabled',
        )

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_afeta_apenas_o_lote_enviado_outro_lote_pendente_permanece(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        outro_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-OUTRO', descricao='Café 1kg', peso_kg='15.000', estado='EM_TRANSFERENCIA',
        )

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote},
        )
        self.assertRedirects(response, reverse('recebimento:transferencia_gestor'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertEqual(outro_palete.estado, 'EM_TRANSFERENCIA')

        mock_notificar.assert_called_once()
        (aprovados,), _ = mock_notificar.call_args
        self.assertEqual([lote for lote, _ in aprovados], [self.palete.lote])

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_ja_vazio_mostra_erro_sem_notificar(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])
        # Simula uma corrida: outro Gestor já aprovou o lote entre o carregamento
        # da página e o clique neste botão — o lote chega vazio ao lock.
        aprovar_lote(self.palete.lote)

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote}, follow=True,
        )

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        mock_notificar.assert_not_called()
        self.assertContains(response, 'Nenhum palete pendente')
        self.assertContains(response, self.palete.lote)

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_aprovar_lote_inexistente_mostra_erro_sem_notificar(self, mock_notificar):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': 'LOTE-NAO-EXISTE'}, follow=True,
        )
        self.assertContains(response, 'Nenhum palete pendente')
        mock_notificar.assert_not_called()

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_novo_palete_do_mesmo_lote_apos_aprovacao_forma_novo_grupo_pendente(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})
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

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_lote_aprovado_hoje_aparece_como_checkbox_para_relatorio(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})

        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertContains(response, 'Gerar Relatório')
        self.assertContains(response, f'value="{self.palete.lote}"')

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    def test_gerar_relatorio_com_lotes_marcados_redireciona_para_o_relatorio(self, mock_notificar):
        self.palete.estado = 'EM_TRANSFERENCIA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor3', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})

        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'gerar_relatorio': '1', 'lote_relatorio': [self.palete.lote],
        })
        self.assertRedirects(
            response,
            reverse('recebimento:relatorio_aprovacao_producao') + f'?lotes={self.palete.lote}',
        )

    def test_gerar_relatorio_sem_nenhum_lote_marcado_mostra_erro(self):
        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'gerar_relatorio': '1'}, follow=True,
        )
        self.assertContains(response, 'Selecione ao menos um lote')

    def test_lote_aprovado_em_outro_dia_nao_aparece_por_padrao(self):
        self.palete.estado = 'CONFERIDO'
        self.palete.aprovado_producao_em = timezone.now() - timezone.timedelta(days=2)
        self.palete.save(update_fields=['estado', 'aprovado_producao_em'])

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertNotContains(response, f'name="lote_relatorio" value="{self.palete.lote}"')

    def test_filtrar_por_data_relatorio_mostra_lotes_daquele_dia(self):
        data_aprovacao = timezone.now() - timezone.timedelta(days=2)
        self.palete.estado = 'CONFERIDO'
        self.palete.aprovado_producao_em = data_aprovacao
        self.palete.save(update_fields=['estado', 'aprovado_producao_em'])

        self.client.login(username='gestor3', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'), {
            'data_relatorio': timezone.localtime(data_aprovacao).date().isoformat(),
        })
        self.assertContains(response, f'name="lote_relatorio" value="{self.palete.lote}"')

    def test_operador_de_outro_setor_recebe_403(self):
        self.client.login(username='operador3b', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        response = self.client.get(reverse('recebimento:transferencia_gestor'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:transferencia_gestor')}")


class TransferenciaLogisticaTests(TestCase):
    """Operador Logística bipa paletes aprovados pela Produção para a fila de
    conferência da logística; Gestor Logística aprova lotes inteiros — só então
    eles ficam visíveis no Mapa de Estoque e elegíveis para put-away."""

    def setUp(self):
        self.operador = User.objects.create_user('operador4', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operador Logística'))

        self.gestor = User.objects.create_user('gestor4', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestor Logística'))

        self.operador_outro_setor = User.objects.create_user('operador4b', password='senha-teste-123')
        self.operador_outro_setor.groups.add(Group.objects.get(name='Operador Produção'))

        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='L-LOG', descricao='Café 500g', peso_kg='20.500', estado='CONFERIDO',
        )

    def test_operador_bipa_palete_conferido_muda_para_em_conferencia_logistica(self):
        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_CONFERENCIA_LOGISTICA')

    def test_gestor_nao_consegue_bipar_palete(self):
        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')

    def test_bipar_palete_ainda_nao_conferido_pela_producao_mostra_erro(self):
        self.palete.estado = 'CRIADO'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CRIADO')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_palete_ja_em_conferencia_logistica_mostra_erro(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])
        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': self.palete.barcode_palete,
        }, follow=True)

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_CONFERENCIA_LOGISTICA')
        self.assertContains(response, 'não pode ser bipado')

    def test_bipar_barcode_inexistente_mostra_erro(self):
        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': 'PLT-NAO-EXISTE',
        }, follow=True)
        self.assertContains(response, 'não encontrado')

    def test_bipar_barcode_vazio_mostra_erro(self):
        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': '',
        }, follow=True)
        self.assertContains(response, 'Bipe ou digite')

    def test_aprovar_lote_muda_para_conferido_logistica_sem_notificar(self):
        outro_palete = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000',
            estado='EM_CONFERENCIA_LOGISTICA',
        )
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        with mock.patch('apps.recebimento.views.disparar_notificacao_async') as mock_notificar:
            response = self.client.post(reverse('recebimento:transferencia_logistica'), {
                'aprovar_lote': self.palete.lote,
            })
            mock_notificar.assert_not_called()
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')
        self.assertEqual(outro_palete.estado, 'CONFERIDO_LOGISTICA')
        self.assertIsNone(self.palete.area_destino)
        self.assertIsNone(outro_palete.area_destino)
        self.assertIsNotNone(self.palete.aprovado_logistica_em)
        self.assertIsNotNone(outro_palete.aprovado_logistica_em)

    def test_operador_nao_consegue_aprovar(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='operador4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote},
        )
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_CONFERENCIA_LOGISTICA')

    def test_aprovar_lote_afeta_apenas_o_lote_enviado_outro_lote_pendente_permanece(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])
        outro_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-LOG-OUTRO', descricao='Café 1kg', peso_kg='15.000',
            estado='EM_CONFERENCIA_LOGISTICA',
        )

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote},
        )
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        outro_palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')
        self.assertEqual(outro_palete.estado, 'EM_CONFERENCIA_LOGISTICA')

    def test_aprovar_lote_incompleto_mostra_erro_sem_afetar_outro_lote_completo(self):
        Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CONFERIDO',
        )
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])
        outro_lote_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='L-LOG-OUTRO', descricao='Café 1kg', peso_kg='15.000',
            estado='EM_CONFERENCIA_LOGISTICA',
        )

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'),
            {'aprovar_lote': self.palete.lote},
            follow=True,
        )

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_CONFERENCIA_LOGISTICA')
        self.assertContains(response, 'aguardando bipagem da logística')

        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': outro_lote_palete.lote},
        )
        outro_lote_palete.refresh_from_db()
        self.assertEqual(outro_lote_palete.estado, 'CONFERIDO_LOGISTICA')

    def test_aprovar_lote_apos_bipar_o_palete_que_faltava_funciona(self):
        pendente = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CONFERIDO',
        )
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote},
        )
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'EM_CONFERENCIA_LOGISTICA')
        self.client.logout()

        self.client.login(username='operador4', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_logistica'), {'barcode_palete': pendente.barcode_palete})
        self.client.logout()

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote},
        )
        self.assertRedirects(response, reverse('recebimento:transferencia_logistica'))

        self.palete.refresh_from_db()
        pendente.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')
        self.assertEqual(pendente.estado, 'CONFERIDO_LOGISTICA')

    def test_lote_ainda_nao_bipado_pela_logistica_ja_aparece_com_zero(self):
        # self.palete já é criado em CONFERIDO no setUp, sem nenhuma bipagem da logística ainda.
        outro_palete_mesmo_lote = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CONFERIDO',
        )

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_logistica'))

        grupos = response.context['grupos']
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['lote'], self.palete.lote)
        self.assertEqual(grupos[0]['quantidade'], 0)
        self.assertEqual(grupos[0]['pendentes_bipagem'], 2)
        self.assertEqual(grupos[0]['total_lote'], 2)
        self.assertCountEqual(
            [p.pk for p in grupos[0]['paletes']], [self.palete.pk, outro_palete_mesmo_lote.pk],
        )
        self.assertContains(response, '0/2')
        self.assertContains(response, 'contador-zerado')
        self.assertContains(response, 'selo-pendente')

    def test_grupo_pendente_mostra_contador_de_bipagem_e_desabilita_botao_do_proprio_card(self):
        Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='18.000', estado='CONFERIDO',
        )
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_logistica'))

        grupos = response.context['grupos']
        self.assertEqual(grupos[0]['pendentes_bipagem'], 1)
        self.assertEqual(grupos[0]['total_lote'], 2)
        self.assertContains(response, '1/2')
        self.assertContains(response, 'contador-parcial')
        self.assertContains(response, 'disabled')

    def test_grupo_completo_mostra_contador_completo_e_botao_habilitado(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_logistica'))

        grupos = response.context['grupos']
        self.assertEqual(grupos[0]['pendentes_bipagem'], 0)
        self.assertEqual(grupos[0]['total_lote'], 1)
        self.assertContains(response, '1/1')
        self.assertContains(response, 'contador-completo')
        self.assertNotContains(
            response, f'name="aprovar_lote" value="{self.palete.lote}" disabled',
        )

    def test_aprovar_lote_ja_vazio_mostra_erro(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])
        # Simula uma corrida: outro Gestor já confirmou o lote entre o carregamento
        # da página e o clique neste botão — o lote chega vazio ao lock.
        aprovar_lote_logistica(self.palete.lote)

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote}, follow=True,
        )

        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')
        self.assertContains(response, 'Nenhum palete pendente')

    def test_aprovar_lote_inexistente_mostra_erro(self):
        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': 'LOTE-NAO-EXISTE'}, follow=True,
        )
        self.assertContains(response, 'Nenhum palete pendente')

    def test_novo_palete_do_mesmo_lote_apos_aprovacao_forma_novo_grupo_pendente(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote},
        )
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')

        novo_palete = Palete.objects.create(
            sku='CAFE-500G', lote=self.palete.lote, descricao='Café 500g', peso_kg='19.000',
            estado='EM_CONFERENCIA_LOGISTICA',
        )
        response = self.client.get(reverse('recebimento:transferencia_logistica'))
        grupos = response.context['grupos']
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['lote'], self.palete.lote)
        self.assertEqual([p.pk for p in grupos[0]['paletes']], [novo_palete.pk])

    def test_lote_aprovado_hoje_aparece_como_checkbox_para_relatorio(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote})

        response = self.client.get(reverse('recebimento:transferencia_logistica'))
        self.assertContains(response, 'Gerar Relatório')
        self.assertContains(response, f'value="{self.palete.lote}"')

    def test_gerar_relatorio_com_lotes_marcados_redireciona_para_o_relatorio(self):
        self.palete.estado = 'EM_CONFERENCIA_LOGISTICA'
        self.palete.save(update_fields=['estado'])

        self.client.login(username='gestor4', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote})

        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'gerar_relatorio': '1', 'lote_relatorio': [self.palete.lote],
        })
        self.assertRedirects(
            response,
            reverse('recebimento:relatorio_aprovacao_logistica') + f'?lotes={self.palete.lote}',
        )

    def test_gerar_relatorio_sem_nenhum_lote_marcado_mostra_erro(self):
        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'gerar_relatorio': '1'}, follow=True,
        )
        self.assertContains(response, 'Selecione ao menos um lote')

    def test_filtrar_por_data_relatorio_mostra_lotes_daquele_dia(self):
        data_aprovacao = timezone.now() - timezone.timedelta(days=2)
        self.palete.estado = 'CONFERIDO_LOGISTICA'
        self.palete.aprovado_logistica_em = data_aprovacao
        self.palete.save(update_fields=['estado', 'aprovado_logistica_em'])

        self.client.login(username='gestor4', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_logistica'), {
            'data_relatorio': timezone.localtime(data_aprovacao).date().isoformat(),
        })
        self.assertContains(response, f'name="lote_relatorio" value="{self.palete.lote}"')

    def test_operador_de_outro_setor_recebe_403(self):
        self.client.login(username='operador4b', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:transferencia_logistica'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        response = self.client.get(reverse('recebimento:transferencia_logistica'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('recebimento:transferencia_logistica')}")


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


class AprovarLoteLogisticaConcorrenciaTests(TransactionTestCase):
    """Mesmo cenário de AprovarLoteConcorrenciaTests, para a etapa de confirmação
    da logística (EM_CONFERENCIA_LOGISTICA -> CONFERIDO_LOGISTICA)."""

    def setUp(self):
        self.lote = 'LT-CONCORRENCIA-LOG'
        self.paletes = [
            Palete.objects.create(
                sku='CAFE-500G', lote=self.lote, descricao='Café 500g', peso_kg='20.000',
                estado='EM_CONFERENCIA_LOGISTICA',
            )
            for _ in range(3)
        ]

    def test_apenas_um_vencedor_aprova_o_lote(self):
        NUM_CONCORRENTES = 5
        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)

        def tentar_aprovar():
            barreira.wait()
            try:
                for _ in range(100):
                    try:
                        aprovar_lote_logistica(self.lote)
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
        self.assertEqual(Palete.objects.filter(lote=self.lote, estado='CONFERIDO_LOGISTICA').count(), 3)


class FluxoCompletoOperadorGestorTests(TestCase):
    """Integra o fluxo real de ponta a ponta com os quatro papéis operacionais:
    Operador Produção envia (imprime na hora) e bipa/aprova a transferência da
    Produção -> Operador Logística bipa/aprova a conferência da Logística e
    guarda (put-away) -> Gestor Produção consulta o Mapa de Estoque -> Operador
    Logística confirma a guarda final.
    Também verifica que cada papel é barrado nas telas exclusivas do outro, e que um
    palete apenas CONFERIDO (Produção aprovou, Logística ainda não confirmou) não
    aparece no Mapa de Estoque nem é aceito no put-away — a etapa que faltava.
    """

    def setUp(self):
        self.operador_producao = User.objects.create_user('operador_producao_fluxo', password='senha-teste-123')
        self.operador_producao.groups.add(Group.objects.get(name='Operador Produção'))

        self.operador_logistica = User.objects.create_user('operador_logistica_fluxo', password='senha-teste-123')
        self.operador_logistica.groups.add(Group.objects.get(name='Operador Logística'))

        self.gestor_producao = User.objects.create_user('gestor_producao_fluxo', password='senha-teste-123')
        self.gestor_producao.groups.add(Group.objects.get(name='Gestor Produção'))

        self.gestor_logistica = User.objects.create_user('gestor_logistica_fluxo', password='senha-teste-123')
        self.gestor_logistica.groups.add(Group.objects.get(name='Gestor Logística'))

        self.endereco = Endereco.objects.create(
            modulo=9, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=9),
            capacidade=21, ocupacao_atual=0,
        )
        self.produto = Produto.objects.create(sku='CAFE-FLUXO', descricao='Café fluxo completo')

    @mock.patch('apps.recebimento.views.disparar_notificacao_async')
    @mock.patch('apps.recebimento.views.disparar_impressao_async')
    def test_fluxo_completo_operador_e_gestor(self, mock_disparar_impressao, mock_disparar_notificacao):
        # 1) Operador Produção envia os dados do palete — etiqueta impressa na hora, fica CRIADO.
        self.client.login(username='operador_producao_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('recebimento:novo'), {
            'produto': self.produto.pk, 'lote': 'LOTE-FLUXO-001', 'quantidade': '1',
        })
        self.assertEqual(response.status_code, 302)
        palete = Palete.objects.get(sku='CAFE-FLUXO')
        self.assertEqual(palete.estado, 'CRIADO')
        self.assertEqual(palete.peso_kg, Decimal('500'))
        mock_disparar_impressao.assert_called_once_with([palete])

        # 2) Operador Produção não pode acessar a conferência da Logística.
        response = self.client.get(reverse('recebimento:transferencia_logistica'))
        self.assertEqual(response.status_code, 403)

        # 3) Operador Produção bipa o palete para a fila de transferência (não pode aprovar)...
        response = self.client.post(reverse('recebimento:transferencia_gestor'), {
            'barcode_palete': palete.barcode_palete,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'EM_TRANSFERENCIA')

        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': palete.lote},
        )
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'EM_TRANSFERENCIA')
        mock_disparar_notificacao.assert_not_called()
        self.client.logout()

        # ...e o Gestor Produção aprova o lote, notificando a logística.
        self.client.login(username='gestor_producao_fluxo', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'aprovar_lote': palete.lote},
        )
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'CONFERIDO')
        self.assertIsNone(palete.area_destino)
        mock_disparar_notificacao.assert_called_once()
        self.client.logout()

        # 3.5) Enquanto só CONFERIDO (Produção aprovou, Logística ainda não confirmou), o
        # palete não conta nas pendências do Mapa de Estoque nem é aceito no put-away —
        # é exatamente essa etapa que faltava antes desta mudança.
        self.client.login(username='gestor_producao_fluxo', password='senha-teste-123')
        mapa = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(mapa.context['total_paletes_pendentes_armazenagem'], 0)
        self.client.logout()

        self.client.login(username='operador_logistica_fluxo', password='senha-teste-123')
        putaway_prematuro = self.client.post(
            reverse('estoque:putaway'), {'barcode_palete': palete.barcode_palete}, follow=True
        )
        self.assertContains(putaway_prematuro, 'ainda não está pendente de armazenagem')
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'CONFERIDO')

        # 4) Operador Logística bipa o palete para a fila de conferência da logística
        # (não pode aprovar)...
        response = self.client.post(reverse('recebimento:transferencia_logistica'), {
            'barcode_palete': palete.barcode_palete,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'EM_CONFERENCIA_LOGISTICA')

        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': palete.lote},
        )
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'EM_CONFERENCIA_LOGISTICA')
        self.client.logout()

        # ...e o Gestor Logística aprova, liberando finalmente para Mapa de Estoque e put-away.
        self.client.login(username='gestor_logistica_fluxo', password='senha-teste-123')
        response = self.client.post(
            reverse('recebimento:transferencia_logistica'), {'aprovar_lote': palete.lote},
        )
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'CONFERIDO_LOGISTICA')
        self.client.logout()

        self.client.login(username='gestor_producao_fluxo', password='senha-teste-123')
        mapa = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(mapa.context['total_paletes_pendentes_armazenagem'], 1)
        self.client.logout()

        # 5) Operador Logística bipa o palete aprovado (Etapa A) e vê os dados do produto.
        self.client.login(username='operador_logistica_fluxo', password='senha-teste-123')
        etapa_a = self.client.post(reverse('estoque:putaway'), {'barcode_palete': palete.barcode_palete})
        self.assertRedirects(etapa_a, f"{reverse('estoque:putaway')}?lote={palete.lote}")

        tela_produto = self.client.get(reverse('estoque:putaway'), {'lote': palete.lote})
        self.assertContains(tela_produto, 'CAFE-FLUXO')
        self.assertContains(tela_produto, self.endereco.codigo)

        # Depois escolhe o endereço (Etapa B) e confirma a guarda do lote inteiro.
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': palete.lote, 'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(palete.estado, 'ARMAZENADO')
        self.assertEqual(palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

        # 6) Operador Produção não pode confirmar a guarda final — isso é exclusivo da Logística.
        self.client.logout()
        self.client.login(username='operador_producao_fluxo', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # 7) Operador Logística confirma com senha e finaliza o palete (sincronizado com o SAP).
        self.client.login(username='operador_logistica_fluxo', password='senha-teste-123')
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-teste-123'})
        self.assertEqual(response.status_code, 302)
        palete.refresh_from_db()
        self.assertEqual(palete.estado, 'FINALIZADO')


class RelatorioAprovacaoProducaoTests(TestCase):

    def setUp(self):
        self.gestor = User.objects.create_user('gestor_relatorio_prod', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestor Produção'))

        self.operador = User.objects.create_user('operador_relatorio_prod', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operador Produção'))

        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='LT-REL-PROD', descricao='Café 500g', peso_kg='20.000', estado='EM_TRANSFERENCIA',
        )
        self.outro_palete = Palete.objects.create(
            sku='CAFE-500G', lote='LT-REL-PROD', descricao='Café 500g', peso_kg='18.000', estado='EM_TRANSFERENCIA',
        )

    def test_relatorio_mostra_dados_agregados_do_lote_e_gestor_correto(self):
        self.client.login(username='gestor_relatorio_prod', password='senha-teste-123')
        with mock.patch('apps.recebimento.views.disparar_notificacao_async'):
            self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})

        response = self.client.get(reverse('recebimento:relatorio_aprovacao_producao'), {'lotes': self.palete.lote})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAFE-500G')
        self.assertContains(response, 'Café 500g')
        self.assertContains(response, 'LT-REL-PROD')
        self.assertContains(response, '38.000 kg')
        self.assertContains(response, 'gestor_relatorio_prod')
        self.assertContains(response, 'Assinatura do Gestor')
        self.assertEqual(response.context['grupos'][0]['quantidade'], 2)
        self.assertContains(response, 'Qtd. Paletes')

    def test_relatorio_com_multiplos_lotes_via_querystring(self):
        outro_lote_palete = Palete.objects.create(
            sku='CAFE-1KG', lote='LT-REL-PROD-2', descricao='Café 1kg', peso_kg='10.000', estado='EM_TRANSFERENCIA',
        )
        self.client.login(username='gestor_relatorio_prod', password='senha-teste-123')
        with mock.patch('apps.recebimento.views.disparar_notificacao_async'):
            self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': self.palete.lote})
            self.client.post(reverse('recebimento:transferencia_gestor'), {'aprovar_lote': outro_lote_palete.lote})

        response = self.client.get(reverse('recebimento:relatorio_aprovacao_producao'), {
            'lotes': f'{self.palete.lote},{outro_lote_palete.lote}',
        })
        self.assertContains(response, 'LT-REL-PROD-2')
        self.assertContains(response, self.palete.lote)

    def test_operador_recebe_403(self):
        self.client.login(username='operador_relatorio_prod', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:relatorio_aprovacao_producao'), {'lotes': self.palete.lote})
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        response = self.client.get(reverse('recebimento:relatorio_aprovacao_producao'), {'lotes': self.palete.lote})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class RelatorioAprovacaoLogisticaTests(TestCase):

    def setUp(self):
        self.gestor = User.objects.create_user('gestor_relatorio_log', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Gestor Logística'))

        self.operador = User.objects.create_user('operador_relatorio_log', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operador Logística'))

        self.palete = Palete.objects.create(
            sku='CAFE-500G', lote='LT-REL-LOG', descricao='Café 500g', peso_kg='20.000',
            estado='EM_CONFERENCIA_LOGISTICA',
        )

    def test_relatorio_mostra_dados_agregados_do_lote_e_gestor_correto(self):
        self.client.login(username='gestor_relatorio_log', password='senha-teste-123')
        self.client.post(reverse('recebimento:transferencia_logistica'), {'aprovar_lote': self.palete.lote})

        response = self.client.get(reverse('recebimento:relatorio_aprovacao_logistica'), {'lotes': self.palete.lote})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAFE-500G')
        self.assertContains(response, 'LT-REL-LOG')
        self.assertContains(response, 'gestor_relatorio_log')

    def test_operador_recebe_403(self):
        self.client.login(username='operador_relatorio_log', password='senha-teste-123')
        response = self.client.get(reverse('recebimento:relatorio_aprovacao_logistica'), {'lotes': self.palete.lote})
        self.assertEqual(response.status_code, 403)
