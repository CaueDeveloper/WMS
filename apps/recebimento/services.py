from django.db import transaction
from django.utils import timezone

from .models import Palete, registrar_historico


class LoteVazioError(Exception):
    pass


class LoteIncompletoError(Exception):
    pass


@transaction.atomic
def aprovar_lote(lote, usuario=None):
    """Aprova em lote todos os paletes EM_TRANSFERENCIA de `lote`, promovendo-os a
    CONFERIDO. Só aprova se nenhum palete impresso para o lote ainda estiver
    CRIADO (aguardando bipagem) — se algum estiver, levanta LoteIncompletoError:
    um lote só é aprovado na transferência da produção depois que todas as
    etiquetas impressas para ele forem bipadas. Não atribui area_destino — essa
    escolha foi adiada para uma etapa futura fora deste fluxo. Retorna o snapshot
    (pré-atualização) dos paletes afetados, usado para montar a notificação
    WhatsApp.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes_do_lote = list(
        Palete.objects.select_for_update().filter(estado__in=['CRIADO', 'EM_TRANSFERENCIA'], lote=lote)
    )
    if not paletes_do_lote:
        raise LoteVazioError(f'Nenhum palete pendente de transferência para o lote {lote}.')

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
