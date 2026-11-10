from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase

from apps.logistica.models import Produto

from .models import EstoqueItemSAP, OrdemProducao
from .sap import SapIndisponivelError


def _usuario(grupo):
    u = User.objects.create_user(grupo.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class FakeSap:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def ordens_producao(self, dias=30):
        return [{'AbsoluteEntry': 1, 'DocumentNumber': 10, 'ItemNo': 'A1', 'ProductDescription': 'Item A',
                 'PlannedQuantity': 100, 'CompletedQuantity': 40, 'ProductionOrderStatus': 'boposReleased',
                 'PostingDate': date.today().isoformat() + 'T00:00:00Z'}]
    def estoque_itens(self, skus): return {'A1': Decimal('55')}


class EstatisticasProducaoTests(TestCase):
    def test_permissoes(self):
        self.client.force_login(_usuario('Operador Produção'))
        self.assertEqual(self.client.get('/recebimento/producao/estatisticas/', secure=True).status_code, 403)
        self.client.force_login(_usuario('Gestor Produção'))
        self.assertEqual(self.client.get('/recebimento/producao/estatisticas/', secure=True).status_code, 200)

    def test_sincronizar(self):
        self.client.force_login(_usuario('Gestor Produção'))
        with mock.patch('apps.producao.sap.SapClient', FakeSap):
            self.client.post('/recebimento/producao/estatisticas/', secure=True)
        self.assertEqual(OrdemProducao.objects.get().quantidade_produzida, 40)
        self.assertEqual(EstoqueItemSAP.objects.get().disponivel, 55)
        r = self.client.get('/recebimento/producao/estatisticas/', secure=True)
        self.assertContains(r, 'Item A')

    def test_sap_fora_do_ar(self):
        self.client.force_login(_usuario('Gestor Produção'))
        with mock.patch('apps.producao.sap.SapClient', side_effect=SapIndisponivelError('x')):
            r = self.client.post('/recebimento/producao/estatisticas/', secure=True, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OrdemProducao.objects.count(), 0)

    def test_filtros_e_ordenacao_das_ops(self):
        from datetime import timedelta
        hoje = date.today()
        for n, (sku, status, dias) in enumerate([('B2', 'Liberada', 1), ('A1', 'Encerrada', 5), ('C3', 'Planejada', 3)]):
            OrdemProducao.objects.create(doc_entry=n, doc_num=n, sku=sku, status=status,
                                         data_lancamento=hoje - timedelta(days=dias),
                                         quantidade_planejada=100, quantidade_produzida=40)
        self.client.force_login(_usuario('Gestor Produção'))
        url = '/recebimento/producao/estatisticas/'

        def skus(**params):
            r = self.client.get(url, params, secure=True)
            return [op.sku for op in r.context['pagina']]
        self.assertEqual(skus(), ['B2', 'C3', 'A1'])  # data mais recente primeiro
        self.assertEqual(skus(ordem='data_asc'), ['A1', 'C3', 'B2'])
        self.assertEqual(skus(ordem='sku_asc'), ['A1', 'B2', 'C3'])
        self.assertEqual(skus(ordem='sku_desc'), ['C3', 'B2', 'A1'])
        self.assertEqual(skus(status='Liberada'), ['B2'])
        self.assertEqual(skus(status='Encerrada'), ['A1'])
        self.assertEqual(skus(status='Planejada'), ['B2', 'C3', 'A1'])  # status fora das opções é ignorado
        self.assertContains(self.client.get(url, secure=True), 'data-anima')

    def test_aba_estoque_por_produto_sem_repetir_por_op(self):
        hoje = date.today()
        for n in range(3):
            OrdemProducao.objects.create(doc_entry=n, doc_num=n, sku='A1', descricao='Item A', status='Liberada',
                                         data_lancamento=hoje, quantidade_planejada=10)
        EstoqueItemSAP.objects.create(sku='A1', disponivel=55)
        EstoqueItemSAP.objects.create(sku='B2', disponivel=9)
        self.client.force_login(_usuario('Gestor Produção'))
        url = '/recebimento/producao/estatisticas/'
        r = self.client.get(url, {'aba': 'estoque'}, secure=True)
        self.assertEqual([(i.sku, i.descricao) for i in r.context['pagina']], [('A1', 'Item A'), ('B2', '')])
        r = self.client.get(url, {'aba': 'estoque', 'ordem': 'saldo_asc'}, secure=True)
        self.assertEqual([i.sku for i in r.context['pagina']], ['B2', 'A1'])
        self.assertEqual([i.sku for i in self.client.get(url, {'aba': 'estoque', 'q': 'item'}, secure=True).context['pagina']], ['A1'])
        self.assertEqual([(i.pct, i.nivel) for i in self.client.get(url, {'aba': 'estoque'}, secure=True).context['pagina']], [(100, 'alto'), (16, 'baixo')])
        self.assertContains(self.client.get(url, {'aba': 'estoque'}, secure=True), 'est-barra')
        # a tabela de OPs não repete mais o disponível
        self.assertNotContains(self.client.get(url, secure=True), 'Disponível')

    def test_saldo_na_tela_de_etiqueta(self):
        Produto.objects.create(sku='A1', descricao='Item A')
        EstoqueItemSAP.objects.create(sku='A1', disponivel=7)
        self.client.force_login(_usuario('Operador Produção'))
        r = self.client.get('/recebimento/producao/', secure=True)
        self.assertEqual(r.context['estoque_sap'], {'A1': '7.000'})


class NotasSaidaSapTests(TestCase):
    """`SapClient.notas_saida_com_carga`: parsing, totais e filtro do Número da Carga."""

    def _nota(self, entry, carga=None, **kw):
        base = {
            'DocEntry': entry, 'DocNum': entry + 100, 'CardCode': 'C1', 'CardName': 'Mercado X',
            'DocDate': '2026-11-01T00:00:00Z', 'DocDueDate': '2026-12-01T00:00:00Z', 'DocTotal': 300.5,
            'U_WB_RouteNumber': carga,
            'DocumentLines': [
                {'LineNum': 0, 'ItemCode': 'CAFE', 'ItemDescription': 'Café', 'Quantity': 10, 'UnitPrice': 20},
                {'LineNum': 1, 'ItemCode': 'ACUCAR', 'ItemDescription': 'Açúcar', 'Quantity': 5.5, 'UnitPrice': 18},
                {'LineNum': 2, 'ItemCode': None, 'Quantity': 1, 'UnitPrice': 1},  # sem item: ignorada
            ],
        }
        base.update(kw)
        return base

    def _consultar(self, brutas):
        from .sap import SapClient
        with mock.patch.object(SapClient, '__init__', lambda self: None), \
                mock.patch.object(SapClient, '_get_todos', return_value=brutas) as chamada:
            notas = SapClient().notas_saida_com_carga()
        return notas, chamada

    def test_normaliza_cabecalho_linhas_e_totais(self):
        notas, chamada = self._consultar([self._nota(1, carga='C-1')])
        nota = notas[0]
        self.assertEqual((nota['docentry'], nota['docnum'], nota['cardcode'], nota['cliente']),
                         (1, 101, 'C1', 'Mercado X'))
        self.assertEqual((nota['data'], nota['vencimento']), ('2026-11-01', '2026-12-01'))
        self.assertEqual((nota['total'], nota['kg_total']), (Decimal('300.5'), Decimal('15.5')))
        self.assertEqual([(ln['linha'], ln['sku'], ln['quantidade'], ln['preco']) for ln in nota['linhas']],
                         [(0, 'CAFE', Decimal('10'), Decimal('20')), (1, 'ACUCAR', Decimal('5.5'), Decimal('18'))])
        self.assertIn("Cancelled eq 'tNO'", chamada.call_args.args[1]['$filter'])

    def test_so_notas_com_numero_da_carga(self):
        notas, _ = self._consultar([self._nota(1, carga='CARGA-9'), self._nota(2, carga=''),
                                    self._nota(3, carga='  '), self._nota(4), self._nota(5, carga=' 77 ')])
        self.assertEqual([(n['docentry'], n['carga']) for n in notas], [(1, 'CARGA-9'), (5, '77')])

    def test_nota_sem_linhas_validas_some(self):
        notas, _ = self._consultar([self._nota(1, carga='C-1', DocumentLines=[{'LineNum': 0, 'ItemCode': 'X', 'Quantity': 0}])])
        self.assertEqual(notas, [])
