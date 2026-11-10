from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.logistica.models import Endereco
from apps.producao.models import Palete, Retirada
from apps.producao.sap import SapIndisponivelError

from .models import Entrega, Romaneio, RomaneioItem
from .services import criar_romaneio


def _palete_armazenado(posicao, corredor='A', andar=1, **kw):
    endereco = Endereco.objects.create(
        corredor=corredor, andar=andar, posicao=posicao, codigo=Endereco.montar_codigo(corredor, andar, posicao),
    )
    return Palete.objects.create(estado='ARMAZENADO', endereco=endereco, **kw)


def _usuario(grupo):
    u = User.objects.create_user(f'u_{grupo}'.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


def _linha(linha, sku, descricao, kg, preco='10.5'):
    return {'linha': linha, 'sku': sku, 'descricao': descricao, 'quantidade': Decimal(kg), 'preco': Decimal(preco)}


def _nota(docentry, linhas, cliente='Mercado X'):
    return {
        'docentry': docentry, 'docnum': docentry + 99, 'carga': f'CARGA-{docentry}', 'cardcode': 'C1', 'cliente': cliente,
        'data': '2026-11-01', 'vencimento': '2026-12-01',
        'total': sum((ln['quantidade'] * ln['preco'] for ln in linhas), Decimal('0')),
        'kg_total': sum((ln['quantidade'] for ln in linhas), Decimal('0')), 'linhas': linhas,
    }


def _fake_sap(notas=None, erro=None):
    class FakeSap:
        def __enter__(self):
            if erro:
                raise erro
            return self

        def __exit__(self, *exc):
            pass

        def notas_saida_com_carga(self):
            return notas

        def notas_saida_resumo(self):
            return [{k: n[k] for k in ('docentry', 'docnum', 'carga', 'cardcode', 'cliente', 'data')}
                    for n in notas or []]

        def nota_saida(self, docentry):
            return next((n for n in notas or [] if n['docentry'] == docentry), None)
    return mock.patch('apps.producao.sap.SapClient', FakeSap)


class RomaneioTests(TestCase):
    def setUp(self):
        cache.clear()
        self.gestor = _usuario('Gestor Logística')
        self.client.force_login(self.gestor)
        self.p1 = _palete_armazenado(1, sku='CAFE', descricao='Café 1KG', lote='L1', peso_kg=500)
        self.p2 = _palete_armazenado(1, corredor='B', sku='ACUCAR', descricao='Açúcar', lote='L2', peso_kg=300)
        self.url = reverse('romaneio:novo')
        self.nota = _nota(1, [_linha(0, 'CAFE', 'Café 1KG', '120.5'), _linha(1, 'ACUCAR', 'Açúcar', '300', '8')])
        self.notas = [self.nota]

    def _dados(self, nota=1, **extra):
        dados = {'data': '2026-10-28', 'nota': nota}
        dados.update(extra)
        return dados

    def _post(self, notas=None, **extra):
        with _fake_sap(self.notas if notas is None else notas):
            return self.client.post(self.url, self._dados(**extra), secure=True)

    def _get(self, notas):
        with _fake_sap(notas):
            return self.client.get(self.url, secure=True)

    def _item(self, notas, docentry):
        """Trecho carregado ao escolher a nota (linhas + plano de estoque)."""
        with _fake_sap(notas):
            return self.client.get(reverse('romaneio:nota_itens', args=[docentry]), secure=True)

    def test_pagina_lista_so_o_cabecalho_das_notas(self):
        r = self._get(self.notas)
        for texto in ('100</span>', 'CARGA-1', 'name="nota" value="1"'):
            self.assertContains(r, texto)
        for texto in ('Mercado X', 'CLI', 'Lançamento'):  # sem cliente nem data na escolha da nota
            self.assertNotContains(r, texto)
        self.assertNotContains(r, 'Café 1KG')  # as linhas só vêm ao escolher a nota

    def test_itens_de_nota_inexistente_ou_ja_romaneada_dao_404(self):
        self.assertEqual(self._item(self.notas, 999).status_code, 404)
        self._post(nota=1)
        self.assertEqual(self._item(self.notas, 1).status_code, 404)

    def test_itens_da_nota_trazem_linhas_e_totais(self):
        r = self._item(self.notas, 1)
        for texto in ('Café 1KG', 'CAFE', 
                      ):
            self.assertContains(r, texto)
        self.assertEqual(r.context['nota']['kg_total'], Decimal('420.5'))
        self.assertNotContains(r, 'R$')
        self.assertNotContains(r, ' kg')  # o peso é cobrado na coleta

    def test_cria_um_romaneio_com_a_nota_inteira(self):
        r = self._post()
        romaneio = Romaneio.objects.get()
        self.assertRedirects(r, reverse('romaneio:detalhe', args=[romaneio.pk]), fetch_redirect_response=False)
        self.assertEqual((romaneio.criado_por, romaneio.status), (self.gestor, 'ABERTO'))
        self.assertEqual([(n.docentry, n.docnum) for n in romaneio.notas.all()], [(1, 100)])
        self.assertEqual(sorted(i.peso_kg for i in romaneio.itens.all()), [Decimal('120.5'), 300])
        self.assertEqual(romaneio.peso_total, Decimal('420.5'))
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual((self.p1.estado, self.p1.peso_kg), ('ARMAZENADO', Decimal('379.5')))
        self.assertEqual((self.p2.estado, self.p2.peso_kg), ('RETIRADO', 0))
        item = romaneio.itens.get(sku='CAFE')
        self.assertEqual((item.nota_linha, item.preco_unitario), (0, Decimal('10.5')))
        self.assertEqual(item.valor, Decimal('10.5') * Decimal('120.5'))
        self.assertEqual(item.retirada.usuario, self.gestor)
        detalhe = self.client.get(reverse('romaneio:detalhe', args=[romaneio.pk]), secure=True)
        for texto in ('Café 1KG', '>100<', f'Romaneio {romaneio.pk} criado.'):
            self.assertContains(detalhe, texto)
        # conferência cega: nada de cliente nem datas da nota
        for texto in ('Mercado X', 'C1', 'Cliente', 'Lançamento', 'Vencimento', '01/11/2026'):
            self.assertNotContains(detalhe, texto)

    def test_varias_notas_no_mesmo_romaneio(self):
        segunda = _nota(2, [_linha(0, 'CAFE', 'Café 1KG', '100')], cliente='Loja Y')
        notas = [self.nota, segunda]
        self._post(notas, nota=[1, 2])
        romaneio = Romaneio.objects.get()
        self.assertEqual([n.docnum for n in romaneio.notas.all()], [100, 101])
        self.assertEqual(romaneio.itens.count(), 3)
        self.assertEqual(romaneio.peso_total, Decimal('520.5'))
        self.assertEqual({i.nota.docnum for i in romaneio.itens.all()}, {100, 101})
        self.assertEqual(self._get(notas).context['notas'], [])  # ambas saem da lista
        lista = self.client.get(reverse('romaneio:lista'), secure=True)
        self.assertContains(lista, '100, 101')

    def test_estoque_e_somado_entre_as_notas_do_romaneio(self):
        a = _nota(2, [_linha(0, 'ACUCAR', 'Açúcar', '200')])
        b = _nota(3, [_linha(0, 'ACUCAR', 'Açúcar', '200')])  # cada uma cabe nos 300 kg, as duas não
        self._post([a, b], nota=[2, 3])
        self.assertEqual(Romaneio.objects.count(), 0)
        self.assertFalse(Retirada.objects.exists())

    def test_nota_ja_romaneada_some_da_lista_e_nao_duplica(self):
        self._post()
        self.assertEqual(self._get(self.notas).context['notas'], [])
        self._post()
        self.assertEqual(Romaneio.objects.count(), 1)

    def test_nota_que_o_mapa_nao_cobre_e_bloqueada_sem_gravar_nada(self):
        sem_milho = _nota(5, [_linha(0, 'CAFE', 'Café 1KG', '100'), _linha(1, 'MILHO', 'Milho', '10')])
        acima = _nota(6, [_linha(0, 'ACUCAR', 'Açúcar', '301')])
        for nota in (sem_milho, acima):
            r = self._item([nota], nota['docentry'])
            self.assertFalse(r.context['nota']['atendivel'])
            self.assertContains(r, 'data-atendivel="0"')
            self.assertContains(r, 'role="alert"')
            self.assertEqual(self._post([nota], nota=nota['docentry']).status_code, 200)
        self.assertEqual(Romaneio.objects.count(), 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.peso_kg, 500)
        self.assertFalse(Retirada.objects.exists())

    def test_faltas_explicam_o_motivo(self):
        sem_milho = _nota(5, [_linha(0, 'MILHO', 'Milho', '10')])
        acima = _nota(6, [_linha(0, 'ACUCAR', 'Açúcar', '301')])
        r = self._item([sem_milho, acima], 5)
        self.assertContains(r, 'Sem estoque no mapa')
        r = self._item([sem_milho, acima], 6)
        self.assertContains(r, 'Estoque insuficiente')

    def test_linhas_do_mesmo_sku_somam_na_checagem(self):
        duas = _nota(7, [_linha(0, 'ACUCAR', 'Açúcar', '200'), _linha(1, 'ACUCAR', 'Açúcar', '200')])
        self._post([duas], nota=7)
        self.assertEqual(Romaneio.objects.count(), 0)  # 400 kg > 300 kg no mapa

    def _fila(self, corredor, andar, n, sku='MILHO'):
        """n paletes de 100 kg no mesmo corredor/andar; o índice 0 é o mais antigo (o último a sair)."""
        base = timezone.now() - timedelta(days=100)
        return [
            _palete_armazenado(
                i + 1, corredor=corredor, andar=andar, sku=sku, descricao=sku.title(), lote=f'M{i}', peso_kg=100,
                armazenado_em=base + timedelta(days=i),
            )
            for i in range(n)
        ]

    def test_saida_automatica_comeca_pelos_paletes_mais_novos(self):
        fila = self._fila('C', 1, 3)
        nota = _nota(8, [_linha(0, 'MILHO', 'Milho', '250')])
        self._post([nota], nota=8)
        for palete in fila:
            palete.refresh_from_db()
        self.assertEqual([(p.estado, p.peso_kg) for p in fila],
                         [('ARMAZENADO', 50), ('RETIRADO', 0), ('RETIRADO', 0)])

    def test_plano_mostra_so_os_paletes_necessarios(self):
        fila = self._fila('C', 1, 3)  # 3 x 100 kg
        nota = _nota(20, [_linha(0, 'MILHO', 'Milho', '150')])
        ln = self._item([nota], 20).context['nota']['linhas'][0]
        self.assertEqual((ln['status'], ln['pct'], ln['faltante']), ('ok', 100, 0))
        self.assertEqual([(p['barcode'], p['kg_usar']) for p in ln['paletes']],
                         [(fila[2].barcode_palete, 100), (fila[1].barcode_palete, 50)])

    def test_plano_parcial_e_linhas_do_mesmo_sku_nao_reusam_palete(self):
        self._fila('C', 1, 2)  # 200 kg
        nota = _nota(21, [_linha(0, 'MILHO', 'Milho', '150'), _linha(1, 'MILHO', 'Milho', '100')])
        n = self._item([nota], 21).context['nota']
        a, b = n['linhas']
        self.assertEqual((a['status'], b['status'], b['pct'], b['faltante']), ('ok', 'parcial', 50, 50))
        self.assertFalse(n['atendivel'])
        self.assertEqual(b['paletes'][0]['kg_usar'], 50)

    def test_coleta_mostra_so_o_proximo_de_cada_corredor_e_andar(self):
        self._fila('C', 1, 3)
        self._fila('D', 2, 1, sku='FEIJAO')
        self._post([_nota(30, [_linha(0, 'MILHO', 'Milho', '250'), _linha(1, 'FEIJAO', 'Feijão', '50')])], nota=30)
        r = Romaneio.objects.get()
        self.assertEqual({(i.corredor, i.andar) for i in r.itens.all()}, {('C', 1), ('D', 2)})
        resp = self.client.get(reverse('romaneio:detalhe', args=[r.pk]), secure=True)
        filas = resp.context['filas']
        self.assertEqual([(f['corredor'], f['andar'], len(f['atras'])) for f in filas], [('C', 1, 2), ('D', 2, 0)])
        self.assertContains(resp, 'Corredor C · Andar 1')
        self.assertContains(resp, '+2 na fila')
        self.assertNotContains(resp, 'Preço')
        self.assertContains(resp, 'data-leitor-qr="barcode_palete"')
        self.assertContains(resp, 'type="hidden" id="barcode_palete"')  # sem digitação do código
        self.assertNotContains(resp, 'M0')  # lote/palete não aparecem

    def test_estoque_preso_atras_de_outro_produto_nao_e_liberado(self):
        self._fila('C', 1, 1)  # MILHO, mais antigo: fica atrás
        _palete_armazenado(
            2, corredor='C', andar=1, sku='FEIJAO', descricao='Feijão', lote='F9', peso_kg=100,
            armazenado_em=timezone.now(),
        )
        nota = _nota(9, [_linha(0, 'MILHO', 'Milho', '10')])
        self.assertContains(self._item([nota], 9), 'Preso por outro produto')
        self._post([nota], nota=9)
        self.assertEqual(Romaneio.objects.count(), 0)

    def test_nota_fora_da_lista_do_sap_e_recusada(self):
        # ex.: a nota perdeu o Número da Carga no SAP depois que a página foi aberta
        self.assertEqual(self._post(nota=99).status_code, 200)
        self.assertEqual(Romaneio.objects.count(), 0)

    def test_sap_indisponivel_nao_cria_nada(self):
        erro = SapIndisponivelError('SAP indisponível: Timeout')
        with _fake_sap(erro=erro):
            self.assertEqual(self.client.get(self.url, secure=True).status_code, 200)
            r = self.client.post(self.url, self._dados(), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Não foi possível consultar as notas no SAP')
        self.assertEqual(Romaneio.objects.count(), 0)

    def test_permissao(self):
        for grupo in ('Operador Logística', 'Gestor Produção'):
            self.client.force_login(_usuario(grupo))
            nomes = ('romaneio:novo',) + (() if grupo == 'Operador Logística' else ('romaneio:lista',))
            for nome in nomes:
                self.assertEqual(self.client.get(reverse(nome), secure=True).status_code, 403)
            self._post()
        self.assertEqual(Romaneio.objects.count(), 0)

    def test_operador_logistica_so_consulta(self):
        self._post()
        r = Romaneio.objects.get()
        self.client.force_login(_usuario('Operador Logística'))
        lista = self.client.get(reverse('romaneio:lista'), secure=True)
        self.assertContains(lista, f'/romaneio/{r.pk}/')
        self.assertNotContains(lista, 'Novo romaneio')
        det = self.client.get(reverse('romaneio:detalhe', args=[r.pk]), secure=True)
        self.assertEqual(det.status_code, 200)
        self.assertContains(det, 'Ver no mapa')  # o operador usa o mapa para achar o palete
        self.assertEqual(self.client.get(reverse('romaneio:imprimir', args=[r.pk]), secure=True).status_code, 200)

    def test_impressao_e_cega_traz_notas_e_itens(self):
        self._post(self.notas + [_nota(2, [_linha(0, 'CAFE', 'Café 1KG', '10')])], nota=[1, 2])
        r = Romaneio.objects.get()
        imp = self.client.get(reverse('romaneio:imprimir', args=[r.pk]), secure=True)
        for texto in ('100, 101', 'Café 1KG', 'Açúcar'):
            self.assertContains(imp, texto)
        for texto in ('Mercado X', 'C1', 'Cliente', 'Lançamento', 'Vencimento', 'R$', 'Preço'):
            self.assertNotContains(imp, texto)

    def test_coleta_e_confirmacao(self):
        self._post(notas=[_nota(1, [_linha(0, 'CAFE', 'Café 1KG', '120.5')])])
        r = Romaneio.objects.get()
        item = r.itens.get()
        barcode = item.retirada.palete.barcode_palete
        op = _usuario('Operador Logística')
        self.client.force_login(op)
        url = reverse('romaneio:coletar', args=[r.pk])
        # Imprimir só depois da coleta finalizada
        self.assertNotContains(self.client.get(reverse('romaneio:detalhe', args=[r.pk]), secure=True), reverse('romaneio:imprimir', args=[r.pk]))
        # confirmar antes de coletar é recusado
        self.client.post(url, {'acao': 'confirmar'}, secure=True)
        r.refresh_from_db()
        self.assertEqual(r.status, 'ABERTO')
        # palete de fora do romaneio
        self.client.post(url, {'barcode_palete': 'XXX'}, secure=True)
        item.refresh_from_db()
        self.assertIsNone(item.coletado_em)
        # bipagem correta, e repetida é recusada
        self.client.post(url, {'barcode_palete': barcode}, secure=True)
        item.refresh_from_db()
        self.assertEqual(item.coletado_por, op)
        primeiro = item.coletado_em
        self.client.post(url, {'barcode_palete': barcode}, secure=True)
        item.refresh_from_db()
        self.assertEqual(item.coletado_em, primeiro)
        # confirmação
        self.client.post(url, {'acao': 'confirmar'}, secure=True)
        r.refresh_from_db()
        self.assertEqual((r.status, r.coleta_confirmada_por), ('COLETADO', op))
        det = self.client.get(reverse('romaneio:detalhe', args=[r.pk]), secure=True)
        self.assertContains(det, 'Coletado')
        self.assertContains(det, reverse('romaneio:imprimir', args=[r.pk]))
        self.assertEqual(self.client.get(url, secure=True).status_code, 405)

    def test_lista_e_menu(self):
        self._post()
        r = self.client.get(reverse('romaneio:lista'), secure=True)
        self.assertNotContains(r, 'Aguardando coleta')  # o status não aparece na lista
        self.assertNotContains(r, 'Inspecionar')
        self.assertNotContains(r, 'Situação')
        self.assertContains(self.client.get(reverse('home'), secure=True), reverse('romaneio:lista'))


class ImpressaoTests(TestCase):
    def setUp(self):
        self.gestor = _usuario('Gestor Logística')
        p = _palete_armazenado(1, sku='CAFE', descricao='Café 1KG', lote='L1', peso_kg=500)
        self.romaneio = criar_romaneio(
            self.gestor, date(2026, 10, 28),
            [(p, Decimal('120'))],
        )
        self.url = reverse('romaneio:imprimir', args=[self.romaneio.pk])

    def test_pagina_traz_logo_e_dados_do_romaneio(self):
        self.client.force_login(self.gestor)
        r = self.client.get(self.url, secure=True)
        self.assertContains(r, 'core/img/familia-do-sitio')
        for texto in ('Romaneio de Carga', 'Café 1KG', 'CAFE', '28/10/2026'):
            self.assertContains(r, texto)
        self.assertNotContains(r, 'window.print();')
        self.assertContains(self.client.get(self.url + '?auto=1', secure=True), 'window.print();')

    def test_detalhe_link_para_mapa_3d_so_se_armazenado(self):
        self.client.force_login(self.gestor)
        url = reverse('romaneio:detalhe', args=[self.romaneio.pk])
        palete = self.romaneio.itens.first().retirada.palete
        link = f'visao=3d&amp;destacar={palete.barcode_palete}'
        self.assertContains(self.client.get(url, secure=True), link)
        palete.estado = 'RETIRADO'
        palete.save(update_fields=['estado'])
        self.assertNotContains(self.client.get(url, secure=True), 'Ver no mapa')

    def test_mapa_dados_destacar_mostra_so_o_palete(self):
        self.client.force_login(self.gestor)
        outro = _palete_armazenado(2, sku='CHA', descricao='Chá', lote='L2', peso_kg=300)
        palete = self.romaneio.itens.first().retirada.palete
        url = reverse('logistica:mapa_estoque_dados')
        todos = self.client.get(url, secure=True).json()['enderecos']
        so_um = self.client.get(url, {'destacar': palete.barcode_palete}, secure=True).json()['enderecos']
        self.assertGreater(len(todos), 1)
        self.assertEqual([e['pl']['b'] for e in so_um], [palete.barcode_palete])
        self.assertNotEqual(palete.pk, outro.pk)

    def test_sem_permissao_e_anonimo(self):
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 302)
        self.client.force_login(_usuario('Operador Produção'))
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 403)


class AdminRomaneioTests(TestCase):
    def setUp(self):
        gestor = _usuario('Gestor Logística')
        p = _palete_armazenado(1, sku='CAFE', descricao='Café 1KG', lote='L1', peso_kg=500)
        self.romaneio = criar_romaneio(
            gestor, date(2026, 10, 28),
            [(p, Decimal('120'))],
        )
        self.url = reverse('admin:romaneio_romaneio_change', args=[self.romaneio.pk])

    def test_superuser_edita(self):
        self.client.force_login(User.objects.create_superuser('root', password='x'))
        self.assertContains(self.client.get(reverse('admin:romaneio_romaneio_changelist'), secure=True), f'/{self.romaneio.pk}/change/')
        r = self.client.get(self.url, secure=True)
        self.assertContains(r, 'name="_save"')
        self.assertContains(r, 'Café 1KG')

    def test_superuser_exclui_romaneio_com_itens(self):
        self.client.force_login(User.objects.create_superuser('root', password='x'))
        url = reverse('admin:romaneio_romaneio_delete', args=[self.romaneio.pk])
        self.assertContains(self.client.get(url, secure=True), 'CAFE')
        Entrega.objects.create(romaneio=self.romaneio, registrada_por=self.romaneio.criado_por,
                               motorista_nome='João', ajudante_nome='Pedro')
        r = self.client.post(url, {'post': 'yes'}, secure=True)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Romaneio.objects.exists())
        self.assertFalse(Entrega.objects.exists())
        self.assertFalse(RomaneioItem.objects.exists())

    def test_staff_so_consulta(self):
        staff = User.objects.create_user('func', password='x', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=('view_romaneio', 'view_romaneioitem')))
        self.client.force_login(staff)
        r = self.client.get(self.url, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="_save"')
