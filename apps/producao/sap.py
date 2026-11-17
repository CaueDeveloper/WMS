"""Cliente somente leitura do SAP B1 Service Layer (apenas GET após o login)."""
import threading
import time
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

STATUS_OP = {
    'boposPlanned': 'Planejada', 'boposReleased': 'Liberada',
    'boposClosed': 'Encerrada', 'boposCancelled': 'Cancelada',
}


class SapIndisponivelError(Exception):
    pass


# Sessão do Service Layer reaproveitada entre requisições (o login custa ~1,5 s).
# Vale por SESSAO_SEGUNDOS, bem abaixo do tempo de vida de 30 min do SAP.
SESSAO_SEGUNDOS = 20 * 60
_sessao = {'cookies': None, 'ate': 0.0}
_sessao_lock = threading.Lock()


class SapClient:
    def __init__(self):
        if requests is None or not (settings.SAP_B1_BASE_URL and settings.SAP_B1_USERNAME):
            raise SapIndisponivelError('Integração SAP não configurada.')
        self.s = requests.Session()
        self.s.verify = settings.SAP_B1_VERIFY_TLS

    def __enter__(self):
        with _sessao_lock:
            if _sessao['cookies'] and time.monotonic() < _sessao['ate']:
                self.s.cookies.update(_sessao['cookies'])
                return self
            self._login()
        return self

    def _login(self):
        r = self._req('post', '/Login', json={
            'CompanyDB': settings.SAP_B1_COMPANY_DB,
            'UserName': settings.SAP_B1_USERNAME,
            'Password': settings.SAP_B1_PASSWORD,
        }, _sem_retry=True)
        if r.status_code != 200:
            _sessao['cookies'] = None
            raise SapIndisponivelError('SAP recusou o login.')
        _sessao['cookies'] = self.s.cookies.get_dict()
        _sessao['ate'] = time.monotonic() + SESSAO_SEGUNDOS

    def __exit__(self, *exc):
        pass  # a sessão fica aberta para a próxima requisição (expira sozinha)

    def _req(self, metodo, caminho, _sem_retry=False, **kw):
        try:
            r = getattr(self.s, metodo)(
                settings.SAP_B1_BASE_URL + caminho, timeout=settings.SAP_B1_TIMEOUT, **kw)
            if r.status_code == 401 and not _sem_retry:
                # Sessão expirada no SAP: entra de novo e repete a chamada uma vez.
                with _sessao_lock:
                    self.s.cookies.clear()
                    self._login()
                r = getattr(self.s, metodo)(
                    settings.SAP_B1_BASE_URL + caminho, timeout=settings.SAP_B1_TIMEOUT, **kw)
            return r
        except requests.RequestException as exc:
            raise SapIndisponivelError(f'SAP indisponível: {exc.__class__.__name__}') from exc

    def _get_todos(self, caminho, params):
        """GET paginado (segue @odata.nextLink)."""
        url, itens = caminho, []
        while url:
            r = self._req('get', url, params=params if url == caminho else None,
                          headers={'Prefer': 'odata.maxpagesize=200'})
            if r.status_code != 200:
                raise SapIndisponivelError(f'SAP retornou {r.status_code} em {caminho}.')
            corpo = r.json()
            itens += corpo.get('value', [])
            link = corpo.get('odata.nextLink') or corpo.get('@odata.nextLink')
            url = '/' + link.lstrip('/').removeprefix('b1s/v2/').removeprefix('b1s/v1/') if link else None
        return itens

    def ordens_producao(self, dias=30):
        desde = (date.today() - timedelta(days=dias)).isoformat()
        return self._get_todos('/ProductionOrders', {
            '$filter': f"PostingDate ge '{desde}'",
            '$select': 'AbsoluteEntry,DocumentNumber,ItemNo,ProductDescription,'
                       'PlannedQuantity,CompletedQuantity,ProductionOrderStatus,PostingDate',
        })

    DIAS_NOTAS_PENDENTES = 7

    @classmethod
    def _filtro_notas_pendentes(cls):
        """Notas lançadas (`DocDate`) nos últimos 7 dias (hoje incluso), emitidas pela
        filial do armazém (`SAP_B1_FILIAL`), não canceladas e com "Número da Carga"
        (`U_WB_RouteNumber`). O dia exato é escolhido na tela, pelo filtro de lançamento."""
        desde = (date.today() - timedelta(days=cls.DIAS_NOTAS_PENDENTES - 1)).isoformat()
        return (f"DocDate ge '{desde}' and BPL_IDAssignedToInvoice eq {int(settings.SAP_B1_FILIAL)}"
                " and CancelStatus eq 'csNo' and U_WB_RouteNumber ne null and U_WB_RouteNumber ne ''")

    def notas_saida_com_carga(self):
        """Notas Fiscais de Saída (/Invoices) pendentes (ver `_filtro_notas_pendentes`):
        [{docentry, docnum, cardcode, cliente, data, vencimento, total, kg_total,
        linhas: [{linha, sku, descricao, quantidade, unidade, preco}]}]. Só leitura (GET)."""
        brutas = self._get_todos('/Invoices', {
            '$filter': self._filtro_notas_pendentes(),
            '$select': 'DocEntry,DocNum,CardCode,CardName,DocDate,DocDueDate,DocTotal,'
                       'U_WB_RouteNumber,DocumentLines',
            '$orderby': 'DocDate desc,DocNum desc',
        })
        return [nota for nota in map(self._nota_de, brutas) if nota]

    def notas_saida_resumo(self):
        """Só o cabeçalho das notas de saída pendentes (sem as linhas, que são ~25x
        mais lentas de baixar): [{docentry, docnum, carga, cardcode, cliente, data}].
        As linhas vêm de `nota_saida`, nota a nota."""
        brutas = self._get_todos('/Invoices', {
            '$filter': self._filtro_notas_pendentes(),
            '$select': 'DocEntry,DocNum,CardCode,CardName,DocDate,U_WB_RouteNumber',
            '$orderby': 'DocDate desc,DocNum desc',
        })
        return [{
            'docentry': n['DocEntry'], 'docnum': n.get('DocNum'),
            'carga': str(n.get('U_WB_RouteNumber') or '').strip(), 'cardcode': n.get('CardCode') or '',
            'cliente': n.get('CardName') or '', 'data': (n.get('DocDate') or '')[:10],
        } for n in brutas if str(n.get('U_WB_RouteNumber') or '').strip()]

    def nota_saida(self, docentry):
        """Uma única Nota Fiscal de Saída (mesmo formato de `notas_saida_com_carga`),
        ou None se não existir, estiver cancelada, for de outra filial ou estiver sem
        carga/linhas. "Cancelada" inclui o documento de cancelamento
        (`CancelStatus = csCancellation`), que também fica em /Invoices: só `csNo` vale.
        A Data de Saída não é checada aqui, para não recusar uma nota escolhida
        pouco antes da virada do dia."""
        r = self._req('get', f'/Invoices({int(docentry)})', params={
            '$select': 'DocEntry,DocNum,CardCode,CardName,DocDate,DocDueDate,DocTotal,CancelStatus,'
                       'BPL_IDAssignedToInvoice,U_WB_RouteNumber,DocumentLines'})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SapIndisponivelError(f'SAP retornou {r.status_code} na nota {docentry}.')
        bruta = r.json()
        if bruta.get('CancelStatus') != 'csNo' or bruta.get('BPL_IDAssignedToInvoice') != int(settings.SAP_B1_FILIAL):
            return None
        return self._nota_de(bruta)

    # DocEntries por consulta em `notas_canceladas` (o $filter vai na URL).
    BLOCO_CANCELADAS = 40

    def notas_canceladas(self, docentries):
        """{DocEntry} das notas informadas que foram canceladas no SAP depois de
        escolhidas (CancelStatus diferente de csNo). Só leitura (GET)."""
        entradas = sorted({int(d) for d in docentries})
        canceladas = set()
        for i in range(0, len(entradas), self.BLOCO_CANCELADAS):
            bloco = entradas[i:i + self.BLOCO_CANCELADAS]
            filtro = ' or '.join(f'DocEntry eq {d}' for d in bloco)
            brutas = self._get_todos('/Invoices', {
                '$filter': f"({filtro}) and CancelStatus ne 'csNo'",
                '$select': 'DocEntry',
            })
            canceladas |= {n['DocEntry'] for n in brutas}
        return canceladas

    @staticmethod
    def _nota_de(n):
        carga = str(n.get('U_WB_RouteNumber') or '').strip()
        if not carga:
            return None  # sem carga: não entra
        linhas = []
        for ln in n.get('DocumentLines') or []:
            quantidade = Decimal(str(ln.get('Quantity') or 0))
            if not ln.get('ItemCode') or quantidade <= 0:
                continue
            linhas.append({
                'linha': ln.get('LineNum'),
                'sku': ln['ItemCode'],
                'descricao': ln.get('ItemDescription') or '',
                'quantidade': quantidade,
                # UoM de venda da linha (kg, un, cx...); casa com Palete.unidade no romaneio.
                'unidade': str(ln.get('UoMCode') or 'kg').strip().lower(),
                'preco': Decimal(str(ln.get('UnitPrice') or 0)),
            })
        if not linhas:
            return None
        return {
            'docentry': n['DocEntry'], 'docnum': n.get('DocNum'),
            'carga': carga, 'cardcode': n.get('CardCode') or '', 'cliente': n.get('CardName') or '',
            'data': (n.get('DocDate') or '')[:10], 'vencimento': (n.get('DocDueDate') or '')[:10],
            'total': Decimal(str(n.get('DocTotal') or 0)),
            'kg_total': sum((ln['quantidade'] for ln in linhas if ln['unidade'] == 'kg'), Decimal('0')),
            'linhas': linhas,
        }

    def estoque_itens(self, skus):
        """{sku: InStock total} apenas dos `skus` pedidos."""
        saldo = {}
        for sku in skus:
            r = self._req('get', f"/Items('{sku}')", params={'$select': 'ItemWarehouseInfoCollection'})
            if r.status_code != 200:
                continue
            saldo[sku] = sum(
                (Decimal(str(w.get('InStock') or 0)) for w in r.json().get('ItemWarehouseInfoCollection', [])),
                Decimal('0'),
            )
        return saldo
