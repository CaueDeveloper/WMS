from django.db import transaction
from django.utils import timezone

from .models import Palete, registrar_historico


class LoteVazioError(Exception):
    pass


class LoteIncompletoError(Exception):
    pass


class PaleteStateError(Exception):
    pass


ESTADOS_REPROVAVEIS = {
    'PRODUCAO': ('CRIADO', 'EM_TRANSFERENCIA'),
    'LOGISTICA': ('CONFERIDO', 'EM_CONFERENCIA_LOGISTICA'),
}


def _bloquear_se_ha_pendentes_na_producao(lote):
    n = Palete.objects.filter(lote=lote, estado__in=('CRIADO', 'EM_TRANSFERENCIA')).count()
    if n:
        raise LoteIncompletoError(
            f'O lote {lote} tem {n} palete(s) ainda em andamento na Produção (corrigido/reprovado) — '
            'aguarde a aprovação deles antes de aprovar o lote na Logística.'
        )


def _bloquear_se_ha_reprovados(lote):
    n = Palete.objects.filter(lote=lote, estado='REPROVADO').count()
    if n:
        raise LoteIncompletoError(
            f'O lote {lote} tem {n} palete(s) reprovado(s) aguardando correção do Gestor — '
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
def corrigir_palete(palete_id, produto, peso_kg, data_fabricacao=None, usuario=None):
    """Aplica a correção do Gestor a um palete REPROVADO e o devolve ao início
    do fluxo (CRIADO — aguardando nova bipagem de transferência)."""
    palete = Palete.objects.select_for_update().filter(pk=palete_id).first()
    if palete is None or palete.estado != 'REPROVADO':
        raise PaleteStateError('Este palete não está mais aguardando correção.')
    # A etiqueta impressa mostra peso e data de fabricação: se mudarem, a
    # view deve reimprimir (palete.reimprimir_etiqueta).
    nova_data = data_fabricacao or palete.data_fabricacao
    palete.reimprimir_etiqueta = (palete.peso_kg != peso_kg) or (palete.data_fabricacao != nova_data)
    palete.sku = produto.sku
    palete.descricao = produto.descricao
    palete.peso_kg = peso_kg
    palete.data_fabricacao = nova_data
    palete.estado = 'CRIADO'
    palete.aprovado_producao_em = None
    palete.reprovado_em = None
    palete.motivo_reprovacao = ''
    palete.reprovado_na_etapa = ''
    palete.save()
    registrar_historico([palete], 'CRIADO', usuario=usuario)
    return palete


@transaction.atomic
def aprovar_lote(lote, usuario=None):
    """Aprova em lote todos os paletes EM_TRANSFERENCIA de `lote`, promovendo-os a
    CONFERIDO. Só aprova se nenhum palete impresso para o lote ainda estiver
    CRIADO (aguardando bipagem) — se algum estiver, levanta LoteIncompletoError:
    um lote só é aprovado na transferência da produção depois que todas as
    etiquetas impressas para ele forem bipadas. Não atribui area_destino — essa
    escolha foi adiada para uma etapa futura fora deste fluxo. Retorna o snapshot
    (pré-atualização) dos paletes afetados.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes_do_lote = list(
        Palete.objects.select_for_update().filter(estado__in=['CRIADO', 'EM_TRANSFERENCIA'], lote=lote)
    )
    if not paletes_do_lote:
        raise LoteVazioError(f'Nenhum palete pendente de transferência para o lote {lote}.')
    _bloquear_se_ha_reprovados(lote)

    pendentes_bipagem = [p for p in paletes_do_lote if p.estado == 'CRIADO']
    if pendentes_bipagem:
        raise LoteIncompletoError(
            f'O lote {lote} ainda tem {len(pendentes_bipagem)} palete(s) aguardando bipagem — '
            'bipe todos os paletes impressos do lote antes de aprovar.'
        )

    ids = [p.pk for p in paletes_do_lote]
    # QuerySet.update() não honra auto_now=True (só Model.save() faz isso), então
    # atualizado_em precisa ser setado explicitamente aqui.
    agora = timezone.now()
    Palete.objects.filter(pk__in=ids).update(
        estado='CONFERIDO', atualizado_em=agora, aprovado_producao_em=agora,
    )
    registrar_historico(paletes_do_lote, 'CONFERIDO', usuario=usuario)
    return paletes_do_lote


@transaction.atomic
def aprovar_lote_logistica(lote, usuario=None):
    """Aprova em lote todos os paletes EM_CONFERENCIA_LOGISTICA de `lote`, promovendo-os
    a CONFERIDO_LOGISTICA. Só aprova se nenhum palete aprovado pela produção para o lote
    ainda estiver CONFERIDO (aguardando bipagem da logística) — se algum estiver, levanta
    LoteIncompletoError: um lote só é confirmado pela logística depois que todos os seus
    paletes forem bipados nesta etapa (mesma regra de aprovar_lote na transferência da
    produção). Não atribui area_destino — essa escolha foi adiada para uma etapa futura
    fora deste fluxo. Retorna o snapshot (pré-atualização) dos paletes afetados.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes_do_lote = list(
        Palete.objects.select_for_update().filter(estado__in=['CONFERIDO', 'EM_CONFERENCIA_LOGISTICA'], lote=lote)
    )
    if not paletes_do_lote:
        raise LoteVazioError(f'Nenhum palete pendente de conferência da logística para o lote {lote}.')
    _bloquear_se_ha_reprovados(lote)
    _bloquear_se_ha_pendentes_na_producao(lote)

    pendentes_bipagem = [p for p in paletes_do_lote if p.estado == 'CONFERIDO']
    if pendentes_bipagem:
        raise LoteIncompletoError(
            f'O lote {lote} ainda tem {len(pendentes_bipagem)} palete(s) aguardando bipagem da logística — '
            'bipe todos os paletes do lote antes de aprovar.'
        )

    ids = [p.pk for p in paletes_do_lote]
    # QuerySet.update() não honra auto_now=True (só Model.save() faz isso), então
    # atualizado_em precisa ser setado explicitamente aqui.
    agora = timezone.now()
    Palete.objects.filter(pk__in=ids).update(
        estado='CONFERIDO_LOGISTICA', atualizado_em=agora, aprovado_logistica_em=agora,
    )
    registrar_historico(paletes_do_lote, 'CONFERIDO_LOGISTICA', usuario=usuario)
    return paletes_do_lote
