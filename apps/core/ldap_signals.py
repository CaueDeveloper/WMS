"""Mapeamento de grupos do AD para os 5 grupos Django do WMS (M18).

AUTH_LDAP_MIRROR_GROUPS criaria um auth.Group por cn do AD, o que não bate
com os nomes usados por @group_required em todo o projeto ("Operador
Produção", "Gestor Logística" etc.) a menos que o AD tenha grupos com esses
nomes exatos — não é o caso aqui. Em vez disso, o usuário autenticado via
LDAP pela primeira vez tem seus grupos Django definidos a partir do DN de grupo do AD a que
pertence, usando o mapeamento AUTH_LDAP_GROUP_DN_MAP (settings.py, montado a
partir das variáveis AUTH_LDAP_GROUP_DN_* do .env).
"""
from django.conf import settings
from django.contrib.auth.models import Group
from django_auth_ldap.backend import populate_user

from .constants import GRUPOS_INATIVOS_POR_PADRAO


def _mapear_grupos_ldap(sender, user, ldap_user, **kwargs):
    mapa = getattr(settings, 'AUTH_LDAP_GROUP_DN_MAP', None)
    if not mapa:
        return

    if user.pk:
        # Só o primeiro acesso puxa o grupo do AD. Depois disso o grupo é do WMS
        # (definido/alterado pelo Administrador na tela de atribuição) e não é
        # sobrescrito a cada login.
        return

    dns_do_usuario = {dn.lower() for dn in ldap_user.group_dns}
    nomes_dos_grupos = {
        nome
        for dn, nomes in mapa.items()
        if dn.lower() in dns_do_usuario
        for nome in nomes
    }

    if nomes_dos_grupos & GRUPOS_INATIVOS_POR_PADRAO:
        user.is_active = False

    user.save()

    if nomes_dos_grupos:
        user.groups.add(*Group.objects.filter(name__in=nomes_dos_grupos))


populate_user.connect(_mapear_grupos_ldap)
