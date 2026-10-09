from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.recebimento.models import Palete, registrar_historico
from apps.recebimento.services import LoteVazioError  # noqa: F401 (reexportado para as views)

from .models import Endereco


class EnderecoCheioError(Exception):
    pass


class PaleteNaoArmazenadoError(Exception):
    pass


@transaction.atomic
def alocar_lote(lote, corredor, andar, usuario=None, permitir_parcial=False):
    """Aloca os paletes CONFERIDO_LOGISTICA de `lote` nas primeiras posições
    livres (por `posicao`) do corredor/andar escolhido pelo usuário no
    Put-away — cada palete ocupa uma posição própria (capacidade 1). Trava as
    linhas do Palete primeiro (antes de travar Endereco), mesma ordem de
    `retirar_palete_do_mapa`, para não abrir risco de deadlock. A escolha de
    quais paletes alocar primeiro (quando nem todos cabem) segue
    `atualizado_em, pk` — mesma convenção de `_buscar_paletes_pendentes_do_lote`.

    Se não há paletes pendentes do lote → LoteVazioError sem alterar nada.
    Se o corredor/andar não existe ou está todo inativo → Endereco.DoesNotExist.

    Com `permitir_parcial=False` (padrão): se as posições livres são menos que
    o lote inteiro → EnderecoCheioError sem alterar nada (tudo ou nada) —
    usado quando o lote cabe inteiro em algum corredor/andar do armazém.

    Com `permitir_parcial=True`: se não há nenhuma posição livre →
    EnderecoCheioError sem alterar nada (garante que quem chama em loop
    sempre avança); caso contrário aloca só os primeiros paletes que couberem
    e deixa o resto CONFERIDO_LOGISTICA — usado quando o lote não cabe inteiro
    em nenhum corredor/andar, para o usuário completar escolhendo outro.

    Retorna (enderecos, paletes) — as posições usadas e os paletes
    efetivamente alocados nesta chamada, na mesma ordem.
    """
    paletes = list(
        Palete.objects.select_for_update()
        .filter(estado='CONFERIDO_LOGISTICA', lote=lote)
        .order_by('atualizado_em', 'pk')
    )
    if not paletes:
        raise LoteVazioError(f'Nenhum palete pendente de armazenagem para o lote {lote}.')

    enderecos = list(
        Endereco.objects.select_for_update()
        .filter(corredor=corredor, andar=andar, ativo=True)
        .order_by('posicao')
    )
    if not enderecos:
        raise Endereco.DoesNotExist(f'Corredor {corredor}, andar {andar} não encontrado ou inativo.')
    # Ocupação lida da tabela de paletes, depois de travar os endereços (a
    # trava serializa alocações concorrentes no mesmo corredor/andar).
    ocupacao = dict(
        Palete.objects.filter(estado='ARMAZENADO', endereco__in=enderecos)
        .values_list('endereco').annotate(n=Count('pk'))
    )
    livres = [e for e in enderecos if ocupacao.get(e.pk, 0) < e.capacidade]

    if permitir_parcial:
        if not livres:
            raise EnderecoCheioError(f'Corredor {corredor}, andar {andar} não tem nenhuma posição livre.')
        paletes = paletes[:len(livres)]
    elif len(livres) < len(paletes):
        raise EnderecoCheioError(
            f'Corredor {corredor}, andar {andar} tem {len(livres)} posição(ões) livre(s), '
            f'mas o lote {lote} tem {len(paletes)} palete(s).'
        )

    destinos = livres[:len(paletes)]
    agora = timezone.now()
    for palete, endereco in zip(paletes, destinos):
        palete.endereco = endereco
        palete.estado = 'ARMAZENADO'
        palete.atualizado_em = agora
        palete.armazenado_em = agora
    Palete.objects.bulk_update(paletes, ['endereco', 'estado', 'atualizado_em', 'armazenado_em'])

    registrar_historico(paletes, 'ARMAZENADO', usuario=usuario)
    return destinos, paletes


@transaction.atomic
def retirar_palete_do_mapa(palete, usuario=None):
    """Retira um palete ARMAZENADO do mapa (saída física): libera a vaga
    (a ocupação é derivada dos paletes) e marca RETIRADO. Trava a própria
    linha do Palete primeiro (mesmo padrão de `aprovar_lote` em
    apps/recebimento/services.py) para que duas ações concorrentes no mesmo
    palete não retirem duas vezes.
    """
    palete = Palete.objects.select_for_update().get(pk=palete.pk)
    if palete.estado != 'ARMAZENADO' or palete.endereco_id is None:
        raise PaleteNaoArmazenadoError(
            f'Palete {palete.barcode_palete} não está mais armazenado '
            '(pode ter sido alterado por outra operação). Atualize e tente novamente.'
        )

    endereco = palete.endereco
    palete.endereco = None
    palete.estado = 'RETIRADO'
    palete.retirado_em = timezone.now()
    palete.save(update_fields=['endereco', 'estado', 'atualizado_em', 'retirado_em'])
    registrar_historico([palete], 'RETIRADO', usuario=usuario)
    return endereco
