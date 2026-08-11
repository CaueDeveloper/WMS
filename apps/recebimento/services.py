from django.db import transaction
from django.utils import timezone

from .models import Palete


class LoteVazioError(Exception):
    pass


@transaction.atomic
def aprovar_lote(lote):
    """Aprova em lote todos os paletes EM_TRANSFERENCIA de `lote`, promovendo-os a
    CONFERIDO. Não atribui area_destino — essa escolha foi adiada para uma etapa
    futura fora deste fluxo. Retorna o snapshot (pré-atualização) dos paletes
    afetados, usado para montar a notificação WhatsApp.

    select_for_update() bloqueia as linhas até o fim da transação: duas aprovações
    concorrentes do mesmo lote nunca processam os mesmos paletes duas vezes — a
    segunda, ao obter o lock, encontra o queryset já vazio e levanta LoteVazioError.
    """
    paletes = list(Palete.objects.select_for_update().filter(estado='EM_TRANSFERENCIA', lote=lote))
    if not paletes:
        raise LoteVazioError(f'Nenhum palete pendente de transferência para o lote {lote}.')

    ids = [p.pk for p in paletes]
    # QuerySet.update() não honra auto_now=True (só Model.save() faz isso), então
    # atualizado_em precisa ser setado explicitamente aqui.
    Palete.objects.filter(pk__in=ids).update(estado='CONFERIDO', atualizado_em=timezone.now())
    return paletes


def aprovar_lotes_pendentes():
    """Aprova, numa única ação do Gestor, todos os lotes atualmente EM_TRANSFERENCIA.
    Reconsulta os lotes distintos no momento da chamada e aprova cada um
    individualmente via aprovar_lote (transação e lock próprios por lote).

    De propósito não há uma transação envolvendo o laço inteiro: um lote esvaziado
    por uma corrida (ex.: aprovação concorrente) é pulado e reportado em `falhas`,
    em vez de desfazer os demais lotes já aprovados nesta mesma chamada.

    Retorna (aprovados, falhas):
      aprovados: list[(lote, paletes)] — lotes aprovados com sucesso.
      falhas: list[(lote, mensagem)] — lotes pulados, com o motivo.
    """
    lotes_pendentes = (
        Palete.objects.filter(estado='EM_TRANSFERENCIA')
        .order_by('lote').values_list('lote', flat=True).distinct()
    )
    aprovados, falhas = [], []
    for lote in lotes_pendentes:
        try:
            paletes = aprovar_lote(lote)
        except LoteVazioError as exc:
            falhas.append((lote, str(exc)))
        else:
            aprovados.append((lote, paletes))
    return aprovados, falhas
