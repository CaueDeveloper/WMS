from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Palete, registrar_historico


class LoteVazioError(Exception):
    pass


class LoteIncompletoError(Exception):
    pass


class PaleteStateError(Exception):
    pass


ESTADOS_REPROVAVEIS = {
    'PRODUCAO': ('FABRICADO', 'TRANSFERENCIA_PRODUCAO'),
    'LOGISTICA': ('APROVADO_PRODUCAO', 'TRANSFERENCIA_LOGISTICA'),
}


def _filtro_lote(lote, sku=None, data_fabricacao=None, **extra):
    """Q dos paletes de um lote; com `sku`, restringe ao produto (lotes compartilhados) e,
    com `data_fabricacao`, à data (paletes sem o campo caem na data de criação, como em
    `Palete.data_fabricacao_exibida`)."""
    filtro = Q(lote=lote, **extra)
    if sku:
        filtro &= Q(sku=sku)
    if data_fabricacao:
        filtro &= Q(data_fabricacao=data_fabricacao) | Q(data_fabricacao__isnull=True, criado_em__date=data_fabricacao)
    return filtro


def _rotulo(lote, sku=None, data_fabricacao=None):
    rotulo = f'{lote} ({sku})' if sku else f'{lote}'
    return f'{rotulo} fabricado em {data_fabricacao:%d/%m/%Y}' if data_fabricacao else rotulo


def _bloquear_se_ha_pendentes_na_producao(lote, sku=None, data_fabricacao=None):
    n = Palete.objects.filter(_filtro_lote(lote, sku, data_fabricacao, estado__in=('FABRICADO', 'TRANSFERENCIA_PRODUCAO'))).count()
    if n:
        raise LoteIncompletoError(
            f'O lote {_rotulo(lote, sku, data_fabricacao)} tem {n} palete(s) ainda em andamento na Produção (corrigido/reprovado) — '
            'aguarde a aprovação deles antes de aprovar o lote na Logística.'
        )


def _bloquear_se_ha_reprovados(lote, sku=None, data_fabricacao=None):
    n = Palete.objects.filter(_filtro_lote(lote, sku, data_fabricacao, estado='REPROVADO')).count()
    if n:
        raise LoteIncompletoError(
            f'O lote {_rotulo(lote, sku, data_fabricacao)} tem {n} palete(s) reprovado(s) aguardando correção do Gestor — '
            'corrija-os antes de aprovar o lote.'
        )


@transaction.atomic
def reprovar_palete(barcode, etapa, usuario=None, motivo=''):
    """Reprova um palete na `etapa` ('PRODUCAO' ou 'LOGISTICA'). Ele passa a
    REPROVADO e fica visível ao Gestor da Produção para correção."""
    palete = Palete.objects.select_for_update().filter(barcode_palete=(barcode or '').strip()).first()
    if palete is None:
        raise PaleteStateError(f'Palete "{barcode}" não encontrado.')
    if palete.estado not in ESTADOS_REPROVAVEIS[etapa]:
        raise PaleteStateError(
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — não pode ser reprovado agora.'
        )
    palete.estado = 'REPROVADO'
    palete.reprovado_em = timezone.now()
    palete.motivo_reprovacao = (motivo or '').strip()
    palete.reprovado_na_etapa = etapa
    palete.save(update_fields=['estado', 'reprovado_em', 'motivo_reprovacao', 'reprovado_na_etapa', 'atualizado_em'])
    registrar_historico([palete], 'REPROVADO', usuario=usuario)
    return palete


@transaction.atomic
def corrigir_palete(palete_id, produto, peso_kg, data_fabricacao=None, usuario=None, data_vencimento=None):
    """Aplica a correção do Gestor a um palete REPROVADO e o devolve ao início
    do fluxo (FABRICADO — aguardando nova bipagem de transferência)."""
    palete = Palete.objects.select_for_update().filter(pk=palete_id).first()
    if palete is None or palete.estado != 'REPROVADO':
        raise PaleteStateError('Este palete não está mais aguardando correção.')
    # A etiqueta impressa mostra peso e datas de fabricação/vencimento: se
    # mudarem, a view deve reimprimir (palete.reimprimir_etiqueta).
    nova_data = data_fabricacao or palete.data_fabricacao
    novo_vencimento = data_vencimento or palete.data_vencimento
    palete.reimprimir_etiqueta = (
        (palete.peso_kg != peso_kg)
        or (palete.data_fabricacao != nova_data)
        or (palete.data_vencimento != novo_vencimento)
    )
    palete.data_vencimento = novo_vencimento
    palete.sku = produto.sku
    palete.descricao = produto.descricao
    palete.peso_kg = peso_kg
    palete.data_fabricacao = nova_data
    palete.estado = 'FABRICADO'
    palete.aprovado_producao_em = None
    palete.reprovado_em = None
    palete.motivo_reprovacao = ''
    palete.reprovado_na_etapa = ''
    palete.save()
    registrar_historico([palete], 'FABRICADO', usuario=usuario)
    return palete


@transaction.atomic
def aprovar_lote(lote, usuario=None, sku=None, data_fabricacao=None):
    """Aprova em lote todos os paletes TRANSFERENCIA_PRODUCAO de `lote`, promovendo-os a
    APROVADO_PRODUCAO. Só aprova se nenhum palete impresso para o lote ainda estiver
    FABRICADO (aguardando bipagem) — se algum estiver, levanta LoteIncompletoError:
    um lote só é aprovado na transferência da produção depois que todas as
    etiquetas impressas para ele forem bipadas. Não atribui area_destino — essa
    escolha foi adiada para uma etapa futura fora deste fluxo. Retorna o snapshot
    (pré-atualização) dos paletes afetados.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes_do_lote = list(
        Palete.objects.select_for_update().filter(_filtro_lote(lote, sku, data_fabricacao, estado__in=['FABRICADO', 'TRANSFERENCIA_PRODUCAO']))
    )
    if not paletes_do_lote:
        raise LoteVazioError(f'Nenhum palete pendente de transferência para o lote {_rotulo(lote, sku, data_fabricacao)}.')
    _bloquear_se_ha_reprovados(lote, sku, data_fabricacao)

    pendentes_bipagem = [p for p in paletes_do_lote if p.estado == 'FABRICADO']
    if pendentes_bipagem:
        raise LoteIncompletoError(
            f'O lote {_rotulo(lote, sku, data_fabricacao)} ainda tem {len(pendentes_bipagem)} palete(s) aguardando bipagem — '
            'bipe todos os paletes impressos do lote antes de aprovar.'
        )

    ids = [p.pk for p in paletes_do_lote]
    # QuerySet.update() não honra auto_now=True (só Model.save() faz isso), então
    # atualizado_em precisa ser setado explicitamente aqui.
    agora = timezone.now()
    Palete.objects.filter(pk__in=ids).update(
        estado='APROVADO_PRODUCAO', atualizado_em=agora, aprovado_producao_em=agora,
    )
    registrar_historico(paletes_do_lote, 'APROVADO_PRODUCAO', usuario=usuario)
    return paletes_do_lote


@transaction.atomic
def aprovar_lote_logistica(lote, usuario=None, sku=None, data_fabricacao=None):
    """Aprova em lote todos os paletes TRANSFERENCIA_LOGISTICA de `lote`, promovendo-os
    a APROVADO_LOGISTICA. Só aprova se nenhum palete aprovado pela produção para o lote
    ainda estiver APROVADO_PRODUCAO (aguardando bipagem da logística) — se algum estiver, levanta
    LoteIncompletoError: um lote só é confirmado pela logística depois que todos os seus
    paletes forem bipados nesta etapa (mesma regra de aprovar_lote na transferência da
    produção). Não atribui area_destino — essa escolha foi adiada para uma etapa futura
    fora deste fluxo. Retorna o snapshot (pré-atualização) dos paletes afetados.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes_do_lote = list(
        Palete.objects.select_for_update().filter(_filtro_lote(lote, sku, data_fabricacao, estado__in=['APROVADO_PRODUCAO', 'TRANSFERENCIA_LOGISTICA']))
    )
    if not paletes_do_lote:
        raise LoteVazioError(f'Nenhum palete pendente de conferência da logística para o lote {_rotulo(lote, sku, data_fabricacao)}.')
    _bloquear_se_ha_reprovados(lote, sku, data_fabricacao)
    _bloquear_se_ha_pendentes_na_producao(lote, sku, data_fabricacao)

    pendentes_bipagem = [p for p in paletes_do_lote if p.estado == 'APROVADO_PRODUCAO']
    if pendentes_bipagem:
        raise LoteIncompletoError(
            f'O lote {_rotulo(lote, sku, data_fabricacao)} ainda tem {len(pendentes_bipagem)} palete(s) aguardando bipagem da logística — '
            'bipe todos os paletes do lote antes de aprovar.'
        )

    ids = [p.pk for p in paletes_do_lote]
    # QuerySet.update() não honra auto_now=True (só Model.save() faz isso), então
    # atualizado_em precisa ser setado explicitamente aqui.
    agora = timezone.now()
    Palete.objects.filter(pk__in=ids).update(
        estado='APROVADO_LOGISTICA', atualizado_em=agora, aprovado_logistica_em=agora,
    )
    registrar_historico(paletes_do_lote, 'APROVADO_LOGISTICA', usuario=usuario)
    return paletes_do_lote


def sincronizar_sap(dias=30):
    """Importa OPs dos últimos `dias` e o saldo dos itens do SAP (somente leitura no SAP).
    Devolve (qtd_ops, qtd_itens); levanta SapIndisponivelError se o SAP falhar."""
    from apps.logistica.models import Produto

    from .models import EstoqueItemSAP, OrdemProducao
    from .sap import STATUS_OP, SapClient

    with SapClient() as sap:
        ops = sap.ordens_producao(dias)
        # Saldo de todo produto ativo, mesmo sem OP nos últimos 30 dias
        skus = {o['ItemNo'] for o in ops if o.get('ItemNo')} | set(
            Produto.objects.filter(ativo=True).values_list('sku', flat=True))
        saldos = sap.estoque_itens(sorted(skus))
    agora = timezone.now()
    with transaction.atomic():
        for o in ops:
            OrdemProducao.objects.update_or_create(doc_entry=o['AbsoluteEntry'], defaults={
                'doc_num': o['DocumentNumber'], 'sku': o['ItemNo'],
                'descricao': o.get('ProductDescription') or '',
                'quantidade_planejada': o.get('PlannedQuantity') or 0,
                'quantidade_produzida': o.get('CompletedQuantity') or 0,
                'status': STATUS_OP.get(o.get('ProductionOrderStatus'), o.get('ProductionOrderStatus') or ''),
                'data_lancamento': o['PostingDate'][:10], 'sincronizado_em': agora,
            })
        for sku, disp in saldos.items():
            EstoqueItemSAP.objects.update_or_create(sku=sku, defaults={'disponivel': disp, 'sincronizado_em': agora})
    return len(ops), len(saldos)
