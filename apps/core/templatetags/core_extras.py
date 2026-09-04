from django import template

register = template.Library()


@register.filter
def em_algum_grupo(user, nomes_separados_por_virgula):
    if user.is_superuser:
        return True
    nomes = [n.strip() for n in nomes_separados_por_virgula.split(',')]
    return user.groups.filter(name__in=nomes).exists()
