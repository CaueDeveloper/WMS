"""Mapeamento de grupos do AD para os 5 grupos Django do WMS (M18).

AUTH_LDAP_MIRROR_GROUPS criaria um auth.Group por cn do AD, o que não bate
com os nomes usados por @group_required em todo o projeto ("Operador
Produção", "Gestor Logística" etc.) a menos que o AD tenha grupos com esses
nomes exatos — não é o caso aqui. Em vez disso, cada usuário autenticado via
LDAP tem seus grupos Django recalculados a partir do DN de grupo do AD a que
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

    if user.pk and getattr(user, 'perfil', None) and user.perfil.atribuido_manualmente:
        # Grupo definido manualmente pelo Administrador (tela de atribuição do
        # AD) — não sobrescrever com o mapeamento automático (que daria
        # Operador+Gestor juntos, ver comentário [TRANSIÇÃO] em settings.py).
        return

    dns_do_usuario = {dn.lower() for dn in ldap_user.group_dns}
    nomes_dos_grupos = {
        nome
        for dn, nomes in mapa.items()
        if dn.lower() in dns_do_usuario
        for nome in nomes
    }

    usuario_novo = not user.pk
    if usuario_novo and nomes_dos_grupos & GRUPOS_INATIVOS_POR_PADRAO:
        user.is_active = False

    if usuario_novo:
        user.save()

    grupos_mapeados = {nome for nomes in mapa.values() for nome in nomes}
    user.groups.remove(*Group.objects.filter(name__in=grupos_mapeados))
    if nomes_dos_grupos:
        grupos = Group.objects.filter(name__in=nomes_dos_grupos)
        user.groups.add(*grupos)


populate_user.connect(_mapear_grupos_ldap)
