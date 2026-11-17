from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def formatar_peso(valor):
    """Peso no padrão brasileiro: vírgula nos decimais e ponto nos milhares
    (500 -> '500,00'; 1000 -> '1.000,00'). Mostra a 3ª casa só se ela existir."""
    if valor in (None, ''):
        return '—'
    try:
        valor = Decimal(str(valor))
    except InvalidOperation:
        return valor
    casas = 3 if valor != valor.quantize(Decimal('0.01')) else 2
    texto = f'{valor:,.{casas}f}'
    return texto.replace(',', '_').replace('.', ',').replace('_', '.')


@register.filter
def peso(valor):
    return formatar_peso(valor)


def formatar_quantidade(valor, unidade='kg'):
    """Quantidade na unidade do palete: kg como peso ('500,00'); un/cx inteiros ('120')."""
    if unidade in ('un', 'cx') and valor not in (None, ''):
        try:
            valor = Decimal(str(valor))
        except InvalidOperation:
            return valor
        if valor == valor.to_integral_value():
            return f'{valor:,.0f}'.replace(',', '.')
    return formatar_peso(valor)


@register.filter
def quantidade(valor, unidade='kg'):
    """`{{ palete.peso_kg|quantidade:palete.unidade }}` -> '500,00 kg' / '120 cx'."""
    unidade = unidade or 'kg'
    return f'{formatar_quantidade(valor, unidade)} {unidade}'


def somar_por_unidade(itens):
    """[(unidade, total)] na ordem kg, un, cx a partir de objetos com peso_kg/unidade."""
    totais = {}
    for item in itens:
        un = getattr(item, 'unidade', None) or 'kg'
        totais[un] = totais.get(un, Decimal('0')) + item.peso_kg
    return [(un, totais[un]) for un in ('kg', 'un', 'cx') if un in totais] + \
        [(un, t) for un, t in totais.items() if un not in ('kg', 'un', 'cx')]


@register.filter
def totais(pares):
    """Lista de (unidade, total) -> '500,00 kg + 120 cx' ('0,00 kg' se vazia)."""
    if not pares:
        return '0,00 kg'
    return ' + '.join(quantidade(total, un) for un, total in pares)


# estado do Palete -> (rótulo curto, classe de badge). O rótulo longo fica no title.
_ESTADO_BADGE = {
    'FABRICADO': ('Fabricado', 'badge-warning'),
    'TRANSFERENCIA_PRODUCAO': ('Transferência Produção', 'badge-warning'),
    'APROVADO_PRODUCAO': ('Aprovado Produção', 'badge-success'),
    'TRANSFERENCIA_LOGISTICA': ('Transferência Logística', 'badge-warning'),
    'APROVADO_LOGISTICA': ('Aprovado Logística', 'badge-success'),
    'ARMAZENADO': ('Armazenado', 'badge-success'),
    'RETIRADO': ('Retirado', 'badge-neutral'),
    'REPROVADO': ('Reprovado', 'badge-danger'),
}


@register.filter
def estado_curto(estado):
    return _ESTADO_BADGE.get(estado, (estado, 'badge-neutral'))[0]


@register.filter
def estado_badge(estado):
    return _ESTADO_BADGE.get(estado, (estado, 'badge-neutral'))[1]
