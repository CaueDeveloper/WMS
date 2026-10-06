from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def em_algum_grupo(user, nomes_separados_por_virgula):
    if user.is_superuser:
        return True
    nomes = [n.strip() for n in nomes_separados_por_virgula.split(',')]
    return user.groups.filter(name__in=nomes).exists()


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
