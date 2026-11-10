from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.logistica.services import retirar_peso_do_palete
from apps.producao.models import Palete

from .models import Romaneio, RomaneioItem, RomaneioNota

ZERO = Decimal('0')
KG = Decimal('0.001')


class RomaneioSemItensError(Exception):
    pass


class QuantidadeIndisponivelError(Exception):
    """O mapa não cobre a quantidade da nota."""


class NotaJaRomaneadaError(Exception):
    pass


# ── Mapa (estoque ARMAZENADO) ────────────────────────────────────────────────

class Filas(dict):
    """Filas por (corredor, andar) + cache dos paletes alcançáveis por SKU."""

    def __init__(self):
        super().__init__()
        self.livres = {}


def _filas_fifo():
    """Paletes ARMAZENADOS por (corredor, andar), do mais novo para o mais antigo
    (o último armazenado sai primeiro). Só dá para retirar o palete da frente da
    fila: se o da frente é de outro SKU, os de trás ficam presos até ele sair."""
    paletes = (
        Palete.objects.filter(estado='ARMAZENADO', endereco__isnull=False).select_related('endereco')
        .annotate(desde=Coalesce('armazenado_em', 'criado_em')).order_by('-desde', '-pk')
    )
    filas = Filas()
    for palete in paletes:
        filas.setdefault((palete.endereco.corredor, palete.endereco.andar), []).append(palete)
    return filas


def _alcancaveis(sku, filas):
    """Paletes do `sku` que a ordem de saída libera hoje (sequência contínua a partir da
    frente de cada fila), na ordem global do mais novo para o mais antigo."""
    cache = getattr(filas, 'livres', None)
    if cache is not None and sku in cache:
        return cache[sku]
    livres = []
    for fila in filas.values():
        for palete in fila:
            if palete.sku != sku:
                break
            livres.append(palete)
    livres = sorted(livres, key=lambda p: (p.desde, p.pk), reverse=True)
    if cache is not None:
        cache[sku] = livres
    return livres


def _estoque_no_mapa(skus):
    """{sku: kg total ARMAZENADO no mapa}."""
    linhas = (
        Palete.objects.filter(estado='ARMAZENADO', endereco__isnull=False, sku__in=skus)
        .values('sku').annotate(total=Sum('peso_kg'))
    )
    return {linha['sku']: linha['total'] for linha in linhas}


# ── Notas fiscais de saída (SAP) x mapa ──────────────────────────────────────

def _faltas_da_nota(nota, filas, estoque):
    """Itens da nota que o mapa não cobre. Linhas do mesmo SKU somam as
    quantidades (o mesmo palete não vale para duas linhas). Devolve
    [{sku, descricao, precisa, liberado, motivo}]; vazio = a nota é atendível."""
    precisa, descricao = {}, {}
    for ln in nota['linhas']:
        precisa[ln['sku']] = precisa.get(ln['sku'], ZERO) + ln['quantidade']
        descricao.setdefault(ln['sku'], ln['descricao'])
    faltas = []
    for sku, qtd in precisa.items():
        liberado = sum((p.peso_kg for p in _alcancaveis(sku, filas)), ZERO)
        if liberado >= qtd:
            continue
        motivo = ('Sem estoque no mapa' if estoque.get(sku, ZERO) <= 0 else
                  'Estoque insuficiente' if estoque[sku] < qtd else 'Preso por outro produto na frente')
        faltas.append({'sku': sku, 'descricao': descricao[sku], 'precisa': qtd, 'liberado': liberado,
                       'motivo': motivo})
    return faltas


def _notas_romaneadas(docentries):
    """Dos `docentries` informados, os que já estão em algum romaneio."""
    return set(RomaneioNota.objects.filter(docentry__in=list(docentries)).values_list('docentry', flat=True))


def resumo_pendente(resumo):
    """Cabeçalhos de notas (sem linhas) que ainda não têm romaneio."""
    ja_feitas = _notas_romaneadas(n['docentry'] for n in resumo)
    return [{**n, 'data': _data(n['data']),
             'busca': f"{n['docnum']} {n['carga']}"}
            for n in resumo if n['docentry'] not in ja_feitas]


def nota_com_disponibilidade(nota):
    """Uma nota completa (com linhas) + plano de estoque; None se já tem romaneio."""
    resultado = notas_com_disponibilidade([nota])
    return resultado[0] if resultado else None


def notas_com_disponibilidade(notas):
    """Notas do SAP que ainda não têm romaneio, cada uma com `faltas` (o que o
    mapa não cobre) e `atendivel`. A checagem é por nota, isolada: o estoque não
    é reservado entre notas, quem cria primeiro leva."""
    ja_feitas = _notas_romaneadas(n['docentry'] for n in notas)
    pendentes = [n for n in notas if n['docentry'] not in ja_feitas]
    skus = {ln['sku'] for n in pendentes for ln in n['linhas']}
    filas, estoque = _filas_fifo(), _estoque_no_mapa(skus)
    resultado = []
    for nota in pendentes:
        faltas = _faltas_da_nota(nota, filas, estoque)
        plano = _plano_da_nota(nota, filas, estoque)
        linhas = [{**ln, 'subtotal': ln['quantidade'] * ln['preco'], **pl}
                  for ln, pl in zip(nota['linhas'], plano)]
        skus = sorted({ln['sku'] for ln in nota['linhas']})
        busca = ' '.join([str(nota['docnum']), str(nota['carga']), *skus,
                          *(ln['descricao'] for ln in nota['linhas'])])
        resultado.append({
            **nota, 'linhas': linhas, 'data': _data(nota['data']), 'vencimento': _data(nota['vencimento']),
            'faltas': faltas, 'atendivel': not faltas, 'skus': skus, 'busca': busca,
        })
    return resultado


def _simular_alocacao(sku, kg, filas, consumido):
    """Divide `kg` entre os paletes liberados do `sku` (mais novos primeiro).
    `consumido` {pk: kg} acumula o que outras linhas do mesmo romaneio já
    reservaram. Devolve ([(palete, kg)], faltante)."""
    falta, alocacao = kg, []
    for palete in _alcancaveis(sku, filas):
        if falta <= 0:
            break
        livre = palete.peso_kg - consumido.get(palete.pk, ZERO)
        if livre <= 0:
            continue
        pegar = min(livre, falta)
        alocacao.append((palete, pegar))
        consumido[palete.pk] = consumido.get(palete.pk, ZERO) + pegar
        falta -= pegar
    return alocacao, max(falta, ZERO)


def _plano_da_nota(nota, filas, estoque):
    """Para cada linha da nota: peso necessário, o que o mapa libera e os
    paletes que seriam usados (só leitura, nada é gravado)."""
    consumido, plano = {}, []
    for ln in nota['linhas']:
        alocacao, faltante = _simular_alocacao(ln['sku'], ln['quantidade'], filas, consumido)
        usado = ln['quantidade'] - faltante
        if faltante <= 0:
            motivo = ''
        elif estoque.get(ln['sku'], ZERO) <= 0:
            motivo = 'Sem estoque no mapa'
        elif estoque[ln['sku']] < ln['quantidade']:
            motivo = 'Estoque insuficiente'
        else:
            motivo = 'Preso por outro produto na frente'
        plano.append({
            'necessario': ln['quantidade'], 'usado': usado, 'faltante': faltante,
            'pct': int(min(usado * 100 / ln['quantidade'], 100)) if ln['quantidade'] > 0 else 100,
            'status': 'ok' if faltante <= 0 else ('parcial' if usado > 0 else 'sem'),
            'motivo': motivo,
            'paletes': [{
                'barcode': p.barcode_palete, 'endereco': p.endereco.codigo, 'lote': p.lote,
                'kg_usar': kg_usar, 'saldo': p.peso_kg, 'desde': p.desde,
            } for p, kg_usar in alocacao],
        })
    return plano


def _alocar_fifo(sku, kg, filas, consumido):
    """Como `_simular_alocacao`, mas levanta erro se o mapa não cobrir `kg`."""
    alocacao, falta = _simular_alocacao(sku, kg, filas, consumido)
    if falta > 0:
        raise QuantidadeIndisponivelError(
            f'O mapa não libera {kg} kg do item {sku} agora (faltam {falta} kg).'
        )
    return alocacao


def _data(texto):
    return date.fromisoformat(texto) if texto else None


@transaction.atomic
def criar_romaneio_de_notas(criado_por, data, notas):
    """Cria UM romaneio com as notas fiscais de saída do SAP informadas, cada uma
    inteira (tudo ou nada): as linhas de todas as notas somam por SKU na checagem
    e são atendidas pelos paletes mais novos; se o mapa não cobrir qualquer item,
    nada é gravado. Cada nota no formato de `SapClient.nota_saida`."""
    if not notas:
        raise RomaneioSemItensError('Escolha ao menos uma nota fiscal.')
    ja_feitas = _notas_romaneadas(n['docentry'] for n in notas)
    if ja_feitas:
        numeros = ', '.join(str(n['docnum']) for n in notas if n['docentry'] in ja_feitas)
        raise NotaJaRomaneadaError(f'Já têm romaneio: nota(s) {numeros}.')
    filas = _filas_fifo()
    linhas = [ln for nota in notas for ln in nota['linhas']]
    faltas = _faltas_da_nota({'linhas': linhas}, filas, _estoque_no_mapa({ln['sku'] for ln in linhas}))
    if faltas:
        raise QuantidadeIndisponivelError('O mapa não cobre as notas: ' + '; '.join(
            f"{f['sku']} precisa de {f['precisa']} kg ({f['motivo'].lower()})" for f in faltas))
    consumido, itens = {}, []
    for nota in notas:
        for ln in nota['linhas']:
            extra = {'nota': nota['docentry'], 'nota_linha': ln['linha'], 'preco_unitario': ln['preco']}
            itens += [(palete, peso, extra)
                      for palete, peso in _alocar_fifo(ln['sku'], ln['quantidade'], filas, consumido)]
    try:
        with transaction.atomic():  # savepoint: a corrida na mesma nota vira erro amigável
            return criar_romaneio(
                criado_por, data, itens,
                notas=[{'docentry': n['docentry'], 'docnum': n['docnum']} for n in notas],
            )
    except IntegrityError as exc:
        raise NotaJaRomaneadaError('Alguma das notas já tem romaneio.') from exc


@transaction.atomic
def criar_romaneio(criado_por, data, itens, notas=(), **cabecalho):
    """Cria o romaneio e, na mesma transação, retira de cada palete o peso
    informado: `itens` é uma lista de (palete, peso_kg) ou (palete, peso_kg,
    dados_da_nota), em que `dados_da_nota['nota']` é o docentry de uma das
    `notas` ({docentry, docnum}). A retirada parcial só acontece aqui. Se qualquer
    item falhar (palete não armazenado, peso acima do saldo —
    PaleteNaoArmazenadoError/PesoInvalidoError), nada é gravado.

    Os paletes são processados em ordem de pk para duas criações simultâneas
    travarem as linhas na mesma ordem (sem deadlock). O mesmo palete pode
    aparecer em mais de uma linha: o saldo é abatido linha a linha."""
    if not itens:
        raise RomaneioSemItensError('Informe ao menos um palete e o peso a retirar.')
    romaneio = Romaneio.objects.create(
        criado_por=criado_por, data=data,
        **cabecalho,
    )
    por_docentry = {n['docentry']: RomaneioNota.objects.create(romaneio=romaneio, **n) for n in notas}
    for palete, peso_kg, *resto in sorted(itens, key=lambda item: item[0].pk):
        endereco = palete.endereco  # lido antes: o palete esvaziado perde o endereço
        retirada = retirar_peso_do_palete(palete, peso_kg, usuario=criado_por)
        posicao = ({'corredor': endereco.corredor, 'andar': endereco.andar, 'posicao': endereco.posicao}
                   if endereco else {})
        dados = dict(resto[0]) if resto else {}
        if 'nota' in dados:
            dados['nota'] = por_docentry[dados['nota']]
        RomaneioItem.objects.create(
            romaneio=romaneio, retirada=retirada, sku=palete.sku, descricao=palete.descricao,
            peso_kg=retirada.peso_kg, **posicao, **dados,
        )
    return romaneio


class ColetaError(Exception):
    pass


@transaction.atomic
def coletar_item(romaneio, barcode, usuario):
    """Registra a coleta de um palete bipado: marca o primeiro item ainda não
    coletado desse palete no romaneio. Devolve o item."""
    from django.utils import timezone

    romaneio = Romaneio.objects.select_for_update().get(pk=romaneio.pk)
    if romaneio.status != 'ABERTO':
        raise ColetaError('A coleta deste romaneio já foi confirmada.')
    barcode = (barcode or '').strip()
    if not barcode:
        raise ColetaError('Bipe ou digite o código do palete.')
    itens = list(romaneio.itens.filter(retirada__palete__barcode_palete=barcode))
    if not itens:
        raise ColetaError(f'O palete "{barcode}" não faz parte deste romaneio.')
    pendente = next((i for i in itens if i.coletado_em is None), None)
    if pendente is None:
        raise ColetaError(f'O palete "{barcode}" já foi coletado.')
    pendente.coletado_em = timezone.now()
    pendente.coletado_por = usuario
    pendente.save(update_fields=['coletado_em', 'coletado_por'])
    return pendente


@transaction.atomic
def confirmar_coleta(romaneio, usuario):
    """Confirma a coleta: só quando todos os itens já foram bipados."""
    from django.utils import timezone

    romaneio = Romaneio.objects.select_for_update().get(pk=romaneio.pk)
    if romaneio.status != 'ABERTO':
        raise ColetaError('A coleta deste romaneio já foi confirmada.')
    faltam = romaneio.itens.filter(coletado_em__isnull=True).count()
    if faltam:
        raise ColetaError(f'Faltam coletar {faltam} item(ns) antes de confirmar.')
    romaneio.status = 'COLETADO'
    romaneio.coleta_confirmada_em = timezone.now()
    romaneio.coleta_confirmada_por = usuario
    romaneio.save(update_fields=['status', 'coleta_confirmada_em', 'coleta_confirmada_por'])
    return romaneio
