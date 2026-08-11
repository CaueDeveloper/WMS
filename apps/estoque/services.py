from django.db import transaction

from apps.recebimento.models import Palete

from .models import Endereco


class EnderecoCheioError(Exception):
    pass


class MesmoEnderecoError(Exception):
    pass


class PaleteNaoArmazenadoError(Exception):
    pass


@transaction.atomic
def alocar_palete(palete, codigo_endereco):
    """Aloca `palete` no endereço `codigo_endereco`.

    `select_for_update()` bloqueia a linha do Endereco até o fim da transação,
    para que duas requisições concorrentes nunca leiam a mesma ocupação e
    ultrapassem a capacidade (D6: rígida de 21 paletes por endereço).
    """
    endereco = Endereco.objects.select_for_update().get(codigo=codigo_endereco, ativo=True)

    if endereco.ocupacao_atual >= endereco.capacidade:
        raise EnderecoCheioError(
            f'Endereço {endereco.codigo} está cheio ({endereco.ocupacao_atual}/{endereco.capacidade}).'
        )

    endereco.ocupacao_atual += 1
    endereco.save(update_fields=['ocupacao_atual'])

    palete.endereco = endereco
    palete.estado = 'ARMAZENADO'
    palete.save(update_fields=['endereco', 'estado', 'atualizado_em'])

    return endereco


@transaction.atomic
def mover_palete(palete, novo_codigo_endereco):
    """Move um palete ARMAZENADO para outro endereço.

    Trava a própria linha do Palete primeiro (mesmo padrão de `aprovar_lote`
    em apps/recebimento/services.py) porque, ao contrário de `alocar_palete`,
    o endereço de origem vem de estado mutável do palete — sem essa trava,
    duas ações concorrentes no mesmo palete poderiam decrementar a ocupação
    da origem duas vezes.
    """
    palete = Palete.objects.select_for_update().get(pk=palete.pk)
    if palete.estado != 'ARMAZENADO' or palete.endereco_id is None:
        raise PaleteNaoArmazenadoError(
            f'Palete {palete.barcode_palete} não está mais armazenado '
            '(pode ter sido alterado por outra operação). Atualize e tente novamente.'
        )

    codigo_atual = Endereco.objects.values_list('codigo', flat=True).get(pk=palete.endereco_id)
    if novo_codigo_endereco == codigo_atual:
        raise MesmoEnderecoError(f'Palete já está no endereço {codigo_atual}.')

    # Lock em ordem determinística evita deadlock quando duas trocas concorrentes
    # disputam os mesmos dois endereços em sentidos opostos.
    codigo_menor, codigo_maior = sorted([codigo_atual, novo_codigo_endereco])
    endereco_menor = Endereco.objects.select_for_update().get(codigo=codigo_menor)
    endereco_maior = Endereco.objects.select_for_update().get(codigo=codigo_maior)
    enderecos = {codigo_menor: endereco_menor, codigo_maior: endereco_maior}
    endereco_atual = enderecos[codigo_atual]
    novo_endereco = enderecos[novo_codigo_endereco]

    if not novo_endereco.ativo:
        raise Endereco.DoesNotExist(f'Endereço {novo_endereco.codigo} está inativo.')
    if novo_endereco.ocupacao_atual >= novo_endereco.capacidade:
        raise EnderecoCheioError(
            f'Endereço {novo_endereco.codigo} está cheio '
            f'({novo_endereco.ocupacao_atual}/{novo_endereco.capacidade}).'
        )

    endereco_atual.ocupacao_atual -= 1
    endereco_atual.save(update_fields=['ocupacao_atual'])
    novo_endereco.ocupacao_atual += 1
    novo_endereco.save(update_fields=['ocupacao_atual'])

    palete.endereco = novo_endereco
    palete.save(update_fields=['endereco', 'atualizado_em'])
    return novo_endereco


@transaction.atomic
def retirar_palete_do_mapa(palete):
    """Retira um palete ARMAZENADO do mapa (saída física): libera a vaga e
    marca RETIRADO. Mesma trava defensiva de `mover_palete` — ver docstring lá.
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
    palete.save(update_fields=['endereco', 'estado', 'atualizado_em'])
    return endereco
