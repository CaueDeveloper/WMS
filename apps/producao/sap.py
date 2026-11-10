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

    def notas_saida_com_carga(self, dias=7):
        """Notas Fiscais de Saída (/Invoices) dos últimos `dias` dias que já têm
        "Número da Carga" (UDF `U_WB_RouteNumber`) preenchido e não estão canceladas:
        [{docentry, docnum, cardcode, cliente, data, vencimento, total, kg_total,
        linhas: [{linha, sku, descricao, quantidade, preco}]}]. Só leitura (GET)."""
        desde = (date.today() - timedelta(days=dias)).isoformat()
        brutas = self._get_todos('/Invoices', {
            '$filter': f"DocDate ge '{desde}' and Cancelled eq 'tNO' and U_WB_RouteNumber ne null and U_WB_RouteNumber ne ''",
            '$select': 'DocEntry,DocNum,CardCode,CardName,DocDate,DocDueDate,DocTotal,'
                       'U_WB_RouteNumber,DocumentLines',
            '$orderby': 'DocDate desc,DocNum desc',
        })
        return [nota for nota in map(self._nota_de, brutas) if nota]

    def notas_saida_resumo(self, dias=7):
        """Só o cabeçalho das notas de saída com carga dos últimos `dias` dias (sem as
        linhas, que são ~25x mais lentas de baixar): [{docentry, docnum, carga,
        cardcode, cliente, data}]. As linhas vêm de `nota_saida`, nota a nota."""
        desde = (date.today() - timedelta(days=dias)).isoformat()
        brutas = self._get_todos('/Invoices', {
            '$filter': f"DocDate ge '{desde}' and Cancelled eq 'tNO' and U_WB_RouteNumber ne null and U_WB_RouteNumber ne ''",
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
        ou None se não existir, estiver cancelada ou sem carga/linhas."""
        r = self._req('get', f'/Invoices({int(docentry)})', params={
            '$select': 'DocEntry,DocNum,CardCode,CardName,DocDate,DocDueDate,DocTotal,Cancelled,'
                       'U_WB_RouteNumber,DocumentLines'})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SapIndisponivelError(f'SAP retornou {r.status_code} na nota {docentry}.')
        bruta = r.json()
        return None if bruta.get('Cancelled') == 'tYES' else self._nota_de(bruta)

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
                'preco': Decimal(str(ln.get('UnitPrice') or 0)),
            })
        if not linhas:
            return None
        return {
            'docentry': n['DocEntry'], 'docnum': n.get('DocNum'),
            'carga': carga, 'cardcode': n.get('CardCode') or '', 'cliente': n.get('CardName') or '',
            'data': (n.get('DocDate') or '')[:10], 'vencimento': (n.get('DocDueDate') or '')[:10],
            'total': Decimal(str(n.get('DocTotal') or 0)),
            'kg_total': sum((ln['quantidade'] for ln in linhas), Decimal('0')),
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
