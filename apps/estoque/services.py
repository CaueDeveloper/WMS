from django.db import transaction
from django.utils import timezone

from apps.recebimento.models import Palete, registrar_historico

from .models import Endereco


class EnderecoCheioError(Exception):
    pass


class PaleteNaoArmazenadoError(Exception):
    pass


class LoteVazioError(Exception):
    pass


@transaction.atomic
def alocar_lote(lote, codigo_endereco, usuario=None, permitir_parcial=False):
    """Aloca os paletes CONFERIDO_LOGISTICA de `lote` no endereço escolhido
    manualmente pelo usuário na grade do Put-away. Trava as linhas do Palete
    primeiro (antes de travar Endereco), mesma ordem de
    `retirar_palete_do_mapa`, para não abrir risco de deadlock. A escolha de
    quais paletes alocar primeiro (quando nem todos cabem) segue
    `atualizado_em, pk` — mesma convenção de `_buscar_paletes_pendentes_do_lote`.

    Se não há paletes pendentes do lote → LoteVazioError sem alterar nada.
    Se o endereço não existe ou está inativo → Endereco.DoesNotExist.

    Com `permitir_parcial=False` (padrão): se as vagas livres do endereço são
    menores que o lote inteiro → EnderecoCheioError sem alterar nada (tudo ou
    nada) — usado quando o lote cabe inteiro em algum endereço do armazém.

    Com `permitir_parcial=True`: se o endereço não tem nenhuma vaga livre →
    EnderecoCheioError sem alterar nada (garante que quem chama em loop
    sempre avança, nunca fica preso alocando zero paletes); caso contrário
    aloca só os primeiros paletes que couberem e deixa o resto
    CONFERIDO_LOGISTICA — usado quando o lote não cabe inteiro em nenhum
    endereço único do armazém, para o usuário completar a armazenagem
    escolhendo mais de um endereço em chamadas sucessivas.

    Retorna (endereco, paletes) — os paletes efetivamente alocados nesta
    chamada, não necessariamente o lote inteiro.
    """
    paletes = list(
        Palete.objects.select_for_update()
        .filter(estado='CONFERIDO_LOGISTICA', lote=lote)
        .order_by('atualizado_em', 'pk')
    )
    if not paletes:
        raise LoteVazioError(f'Nenhum palete pendente de armazenagem para o lote {lote}.')

    endereco = Endereco.objects.select_for_update().get(codigo=codigo_endereco, ativo=True)

    vagas = endereco.capacidade - endereco.ocupacao_atual
    if permitir_parcial:
        if vagas <= 0:
            raise EnderecoCheioError(f'Endereço {endereco.codigo} não tem nenhuma vaga livre.')
        paletes = paletes[:vagas]
    elif vagas < len(paletes):
        raise EnderecoCheioError(
            f'Endereço {endereco.codigo} tem {vagas} vaga(s) livre(s), mas o lote {lote} '
            f'tem {len(paletes)} palete(s).'
        )

    agora = timezone.now()
    for palete in paletes:
        palete.endereco = endereco
        palete.estado = 'ARMAZENADO'
        palete.atualizado_em = agora
        palete.armazenado_em = agora
    Palete.objects.bulk_update(paletes, ['endereco', 'estado', 'atualizado_em', 'armazenado_em'])

    endereco.ocupacao_atual += len(paletes)
    endereco.save(update_fields=['ocupacao_atual'])

    registrar_historico(paletes, 'ARMAZENADO', usuario=usuario)
    return endereco, paletes


@transaction.atomic
def retirar_palete_do_mapa(palete, usuario=None):
    """Retira um palete ARMAZENADO do mapa (saída física): libera a vaga e
    marca RETIRADO. Trava a própria linha do Palete primeiro (mesmo padrão de
    `aprovar_lote` em apps/recebimento/services.py) porque, ao contrário de
    `alocar_lote`, o endereço de origem vem de estado mutável do palete — sem
    essa trava, duas ações concorrentes no mesmo palete poderiam decrementar
    a ocupação da origem duas vezes.
    """
    palete = Palete.objects.select_for_update().get(pk=palete.pk)
    if palete.estado != 'ARMAZENADO' or palete.endereco_id is None:
        raise PaleteNaoArmazenadoError(
            f'Palete {palete.barcode_palete} não está mais armazenado '
            '(pode ter sido alterado por outra operação). Atualize e tente novamente.'
        )

    endereco = Endereco.objects.select_for_update().get(pk=palete.endereco_id)
    endereco.ocupacao_atual = max(endereco.ocupacao_atual - 1, 0)
    endereco.save(update_fields=['ocupacao_atual'])

    palete.endereco = None
    palete.estado = 'RETIRADO'
    palete.retirado_em = timezone.now()
    palete.save(update_fields=['endereco', 'estado', 'atualizado_em', 'retirado_em'])
    registrar_historico([palete], 'RETIRADO', usuario=usuario)
    return endereco
