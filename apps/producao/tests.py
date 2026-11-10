from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.logistica.models import Produto

from .models import Palete
from .services import LoteIncompletoError, PaleteStateError, aprovar_lote, corrigir_palete, reprovar_palete


def _usuario(grupo):
    u = User.objects.create_user(f'u_{grupo}'.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class ReceberPaleteTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.client.force_login(_usuario('Operador Produção'))
        self.url = reverse('producao:novo')
        self.hoje = timezone.localdate()

    def _post(self, **extra):
        dados = {'produto': self.produto.pk, 'lote': 'l1', 'quantidade': 2,
                 'data_fabricacao': self.hoje.isoformat(),
                 'data_vencimento': (self.hoje + timedelta(days=180)).isoformat()}
        dados.update(extra)
        with mock.patch('apps.producao.views.disparar_impressao_async'):
            return self.client.post(self.url, dados, secure=True)

    def test_data_fabricacao_obrigatoria(self):
        r = self._post(data_fabricacao='')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_data_fabricacao_futura_rejeitada(self):
        r = self._post(data_fabricacao=(self.hoje + timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_data_vencimento_obrigatoria(self):
        r = self._post(data_vencimento='')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_vencimento_anterior_a_fabricacao_rejeitado(self):
        r = self._post(data_vencimento=(self.hoje - timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_peso_padrao_500_e_data_gravada(self):
        r = self._post()
        self.assertEqual(r.status_code, 302)
        paletes = Palete.objects.all()
        self.assertEqual(paletes.count(), 2)
        for p in paletes:
            self.assertEqual(p.peso_kg, Decimal('500'))
            self.assertEqual(p.data_fabricacao, self.hoje)
            self.assertEqual(p.data_vencimento, self.hoje + timedelta(days=180))

    def test_peso_variavel_exige_peso_do_ultimo(self):
        self.assertEqual(self._post(peso_variavel='on').status_code, 200)
        self.assertEqual(self._post(peso_variavel='on', peso_ultimo='0').status_code, 200)
        self.assertEqual(self._post(peso_variavel='on', peso_ultimo='').status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_so_o_ultimo_palete_tem_peso_proprio(self):
        self.assertEqual(self._post(peso_variavel='on', peso_ultimo='123.5').status_code, 302)
        self.assertEqual(
            sorted(Palete.objects.values_list('peso_kg', flat=True)), [Decimal('123.5'), Decimal('500')]
        )

    def test_peso_ultimo_ignorado_sem_peso_variavel(self):
        self.assertEqual(self._post(peso_ultimo='1').status_code, 302)
        self.assertEqual(set(Palete.objects.values_list('peso_kg', flat=True)), {Decimal('500')})

    def test_sucesso_usa_tela_cheia(self):
        r = self._post()
        r = self.client.get(self.url, secure=True)
        self.assertContains(r, 'id="sucesso-tela"')
        self.assertContains(r, 'dlg-confirmar')


class ReprovacaoTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.p = Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='TRANSFERENCIA_PRODUCAO')

    def test_reprovar_e_corrigir(self):
        op = _usuario('Operador Produção')
        reprovar_palete(self.p.barcode_palete, 'PRODUCAO', usuario=op, motivo='peso errado')
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        self.assertEqual(self.p.motivo_reprovacao, 'peso errado')
        self.assertTrue(self.p.historico.filter(estado='REPROVADO').exists())

        corrigir_palete(self.p.pk, self.produto, Decimal('480'), timezone.localdate(), usuario=op)
        self.p.refresh_from_db()
        self.assertEqual((self.p.estado, self.p.sku, self.p.peso_kg), ('FABRICADO', 'CAFE', Decimal('480')))

    def test_estado_invalido_nao_reprova(self):
        self.p.estado = 'APROVADO_PRODUCAO'
        self.p.save()
        with self.assertRaises(PaleteStateError):
            reprovar_palete(self.p.barcode_palete, 'PRODUCAO')
        reprovar_palete(self.p.barcode_palete, 'LOGISTICA')  # logística aceita APROVADO_PRODUCAO

    def test_lote_com_reprovado_nao_aprova(self):
        Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='TRANSFERENCIA_PRODUCAO')
        reprovar_palete(self.p.barcode_palete, 'PRODUCAO')
        with self.assertRaises(LoteIncompletoError):
            aprovar_lote('L1')

    def test_views_e_permissoes(self):
        self.client.force_login(_usuario('Operador Produção'))
        r = self.client.post(reverse('producao:transferencia_gestor'),
                             {'reprovar_palete': self.p.barcode_palete, 'motivo': 'x'}, secure=True)
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        url = reverse('producao:transferencia_gestor')
        dados = {'corrigir_palete': self.p.pk, 'produto': self.produto.pk, 'peso_kg': '500'}
        # Operador não corrige; o reprovado aparece no lote do Gestor Produção, que corrige
        self.client.post(url, dados, secure=True)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        self.client.force_login(_usuario('Gestor Produção'))
        r = self.client.get(url, secure=True)
        self.assertContains(r, self.p.barcode_palete)
        self.client.post(url, dados, secure=True)
        self.p.refresh_from_db()
        self.assertEqual((self.p.estado, self.p.sku, self.p.peso_kg), ('FABRICADO', 'CAFE', Decimal('500')))

    def test_gestor_reprova_pela_planilha(self):
        self.client.force_login(_usuario('Gestor Produção'))
        r = self.client.post(reverse('producao:transferencia_gestor'),
                             {'reprovar_palete': self.p.barcode_palete}, secure=True)
        self.assertIn(f'flash=palete_erro:{self.p.barcode_palete}', r['Location'])
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')


class TransferenciaConfirmacaoTests(TestCase):
    def test_bipagem_redireciona_com_animacao_na_linha_e_confirmacao(self):
        p = Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='FABRICADO')
        self.client.force_login(User.objects.create_user('adm', password='x', is_superuser=True))
        url = reverse('producao:transferencia_gestor')
        r = self.client.post(url, {'barcode_palete': p.barcode_palete}, secure=True)
        self.assertEqual(r['Location'], f'{url}?flash=palete_ok:{p.barcode_palete}')
        r = self.client.get(url, secure=True)
        self.assertNotContains(r, 'id="sucesso-tela"')
        self.assertContains(r, 'data-linha-palete')
        self.assertContains(r, 'dlg-confirmar-lote')
        self.assertContains(r, 'data-confirmar-lote')
        r = self.client.post(url, {'aprovar_lote': 'L1'}, secure=True)
        self.assertIn('flash=lote_ok:L1', r['Location'])
        r = self.client.get(url, secure=True)
        self.assertNotContains(r, 'id="sucesso-tela"')


class LoteCompartilhadoTests(TestCase):
    """Dois produtos no mesmo lote: a aprovação é por produto."""

    def setUp(self):
        self.a = Palete.objects.create(sku='A', descricao='Prod A', lote='L1', peso_kg=1, estado='TRANSFERENCIA_PRODUCAO',
                                       data_fabricacao=date(2026, 3, 5))
        self.b = Palete.objects.create(sku='B', descricao='Prod B', lote='L1', peso_kg=1, estado='TRANSFERENCIA_PRODUCAO')
        self.url = reverse('producao:transferencia_gestor')
        self.client.force_login(User.objects.create_user('adm', password='x', is_superuser=True))

    def test_aprova_so_o_produto(self):
        aprovar_lote('L1', sku='A')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.estado, self.b.estado), ('APROVADO_PRODUCAO', 'TRANSFERENCIA_PRODUCAO'))

    def test_reprovado_de_outro_produto_nao_bloqueia(self):
        self.b.estado = 'REPROVADO'
        self.b.save()
        aprovar_lote('L1', sku='A')
        with self.assertRaises(LoteIncompletoError):
            aprovar_lote('L1', sku='B')

    def test_tela_mostra_fabricacao_e_aprova_por_produto(self):
        r = self.client.get(self.url, secure=True)
        self.assertContains(r, '05/03/2026')
        r = self.client.post(self.url, {'aprovar_lote': 'L1', 'aprovar_sku': 'A'}, secure=True)
        self.assertIn('flash=lote_ok:L1%7CA', r['Location'])
        self.b.refresh_from_db()
        self.assertEqual(self.b.estado, 'TRANSFERENCIA_PRODUCAO')


class DataFabricacaoTests(TestCase):
    """Mesmo lote e produto com duas datas de fabricação: grupos e aprovação separados."""

    def setUp(self):
        for dia in (5, 6):
            Palete.objects.create(sku='A', descricao='Prod A', lote='L1', peso_kg=1, estado='TRANSFERENCIA_PRODUCAO',
                                  data_fabricacao=date(2026, 3, dia))
        self.url = reverse('producao:transferencia_gestor')
        self.client.force_login(User.objects.create_user('adm', password='x', is_superuser=True))

    def test_um_grupo_por_data(self):
        r = self.client.get(self.url, secure=True)
        self.assertEqual([g['data_fabricacao'] for g in r.context['grupos']], [date(2026, 3, 5), date(2026, 3, 6)])

    def test_aprova_so_a_data(self):
        aprovar_lote('L1', sku='A', data_fabricacao=date(2026, 3, 5))
        self.assertEqual(
            list(Palete.objects.order_by('data_fabricacao').values_list('estado', flat=True)),
            ['APROVADO_PRODUCAO', 'TRANSFERENCIA_PRODUCAO'],
        )


class PreviewEtiquetaTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.url = reverse('producao:preview_etiqueta')
        self.dados = {
            'produto': self.produto.pk, 'lote': 'l1', 'quantidade': 2,
            'data_fabricacao': timezone.localdate().isoformat(),
            'data_vencimento': (timezone.localdate() + timedelta(days=180)).isoformat(),
        }

    def test_devolve_png_sem_criar_paletes(self):
        self.client.force_login(_usuario('Operador Produção'))
        resp = self.client.post(self.url, self.dados, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')
        self.assertTrue(resp.content.startswith(b'\x89PNG'))
        self.assertEqual(Palete.objects.count(), 0)

    def test_dados_invalidos_devolve_400(self):
        self.client.force_login(_usuario('Operador Produção'))
        resp = self.client.post(self.url, {**self.dados, 'lote': '@@'}, secure=True)
        self.assertEqual(resp.status_code, 400)

    def test_get_nao_permitido(self):
        self.client.force_login(_usuario('Operador Produção'))
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 405)

    def test_sem_permissao_nao_gera(self):
        self.client.force_login(_usuario('Logística'))
        self.assertNotEqual(self.client.post(self.url, self.dados, secure=True).status_code, 200)

    def test_matriz_qr_e_quadrada(self):
        from .etiqueta_preview import _matriz_qr
        matriz = _matriz_qr('PLT-0123456789AB')
        self.assertEqual(len(matriz), len(matriz[0]))
        self.assertEqual(len(matriz), 21)  # versão 1 (nível Q cabe 16 caracteres alfanuméricos)

    def test_zpl_tem_logo_qr_sku_e_descricao_escapados(self):
        from .zpl import montar_zpl_etiqueta
        palete = Palete(
            sku='PA000003', descricao='Café Gourmet ^1 KG', lote='L1', peso_kg=Decimal('500'),
            data_vencimento=date(2027, 3, 15),
        )
        zpl = montar_zpl_etiqueta(palete)
        self.assertIn('^FD15/03/2027^FS', zpl)
        self.assertIn('VENCIMENTO', zpl)
        self.assertIn('^GFA,', zpl)
        self.assertIn('^BQN,2,10', zpl)
        self.assertIn(f'^FDLA,{palete.barcode_palete}^FS', zpl)
        self.assertIn('SKU: PA000003', zpl)
        self.assertIn(r'Caf\C3\A9 Gourmet \5E1 KG', zpl)
        self.assertNotIn('^BC', zpl)
        self.assertNotIn('[[', zpl)

    def test_escapar_zpl(self):
        from .zpl import escapar_zpl
        self.assertEqual(escapar_zpl('A^B~C\\D'), r'A\5EB\7EC\5CD')
        self.assertEqual(escapar_zpl('Sítio'), r'S\C3\ADtio')

    def test_quebra_descricao_e_ajuste_do_lote(self):
        from .zpl import ajustar_lote, quebrar_descricao
        linhas = quebrar_descricao('GRAO T.GOURMET FRUTTATO BLEND 1 KG - CAFE DO SITIO')
        self.assertEqual(len(linhas), 2)
        self.assertTrue(all(len(linha) <= 42 for linha in linhas))
        longa = quebrar_descricao('PALAVRA ' * 30)
        self.assertEqual(len(longa), 2)
        self.assertTrue(longa[1].endswith('...'))
        self.assertEqual(ajustar_lote('L1'), ('L1', 40))
        texto, altura = ajustar_lote('X' * 60)
        self.assertEqual((len(texto), altura), (26, 26))

    def test_logo_zpl_bytes_declarados_conferem(self):
        import re
        from .zpl import logo_zpl
        total, total2, por_linha, dados = re.search(r'\^GFA,(\d+),(\d+),(\d+),([0-9A-F]+)', logo_zpl()).groups()
        self.assertEqual(total, total2)
        self.assertEqual(len(dados) // 2, int(total))
        self.assertEqual(int(total) % int(por_linha), 0)


class AprovacaoPorProdutoTests(TestCase):
    """Dois produtos no mesmo lote são aprovados separadamente."""

    def setUp(self):
        self.a = Palete.objects.create(sku='A', descricao='Prod A', lote='L1', peso_kg=1,
                                       estado='TRANSFERENCIA_PRODUCAO', data_fabricacao=date(2026, 3, 5))
        self.b = Palete.objects.create(sku='B', descricao='Prod B', lote='L1', peso_kg=1,
                                       estado='TRANSFERENCIA_PRODUCAO', data_fabricacao=date(2026, 3, 6))

    def test_aprova_so_o_produto(self):
        aprovar_lote('L1', sku='A')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.estado, self.b.estado), ('APROVADO_PRODUCAO', 'TRANSFERENCIA_PRODUCAO'))

    def test_reprovado_de_outro_produto_nao_bloqueia(self):
        reprovar_palete(self.b.barcode_palete, 'PRODUCAO')
        aprovar_lote('L1', sku='A')
        with self.assertRaises(LoteIncompletoError):
            aprovar_lote('L1', sku='B')

    def test_tela_mostra_fabricacao_e_view_aprova_por_sku(self):
        self.client.force_login(User.objects.create_user('adm', password='x', is_superuser=True))
        url = reverse('producao:transferencia_gestor')
        r = self.client.get(url, secure=True)
        self.assertContains(r, '05/03/2026')
        self.assertContains(r, '06/03/2026')
        r = self.client.post(url, {'aprovar_lote': 'L1', 'aprovar_sku': 'A'}, secure=True)
        self.assertIn('flash=lote_ok:L1%7CA', r['Location'])
        self.b.refresh_from_db()
        self.assertEqual(self.b.estado, 'TRANSFERENCIA_PRODUCAO')


class AdminPaleteEdicaoTests(TestCase):
    def setUp(self):
        self.palete = Palete.objects.create(sku='CAFE', descricao='Café', lote='L1', peso_kg=500)
        self.url = reverse('admin:producao_palete_change', args=[self.palete.pk])

    def _dados(self, **extra):
        dados = {
            'sku': 'CAFE', 'descricao': 'Café', 'lote': 'L2', 'peso_kg': '450',
            'data_fabricacao': '', 'data_vencimento': '', 'estado': 'FABRICADO',
            'area_destino': '', 'endereco': '',
        }
        dados.update(extra)
        return dados

    def test_superuser_edita_e_estado_novo_entra_no_historico(self):
        self.client.force_login(User.objects.create_superuser('root', password='x'))
        self.assertContains(self.client.get(self.url, secure=True), 'name="_save"')
        r = self.client.post(self.url, self._dados(estado='REPROVADO'), secure=True)
        self.assertEqual(r.status_code, 302)
        self.palete.refresh_from_db()
        self.assertEqual((self.palete.lote, self.palete.estado), ('L2', 'REPROVADO'))
        self.assertTrue(self.palete.historico.filter(estado='REPROVADO').exists())

    def test_demais_usuarios_so_consultam(self):
        from django.contrib.auth.models import Permission
        staff = User.objects.create_user('func', password='x', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=('view_palete', 'change_palete')))
        self.client.force_login(staff)
        r = self.client.get(self.url, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="_save"')
