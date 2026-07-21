from django.db import transaction

from .models import Endereco


class EnderecoCheioError(Exception):
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
