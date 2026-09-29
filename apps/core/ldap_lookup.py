"""Busca de membros dos grupos do AD (Produção/Logística) ainda sem usuário
Django — usado pela tela de atribuição manual (Administradores). Import do
python-ldap protegido pelo mesmo motivo de settings.py: sem wheel oficial
para Windows, o projeto deve continuar rodando sem essa dependência.
"""
from django.conf import settings
from django.contrib.auth import get_user_model

try:
    import ldap
    _LDAP_DISPONIVEL = True
except ImportError:
    _LDAP_DISPONIVEL = False

AREAS_AD = {
    'PRODUCAO': {
        'label': 'Produção',
        'group_dn_env': 'AUTH_LDAP_GROUP_DN_PRODUCAO',
        'grupo_gestor': 'Gestor Produção',
        'grupo_colaborador': 'Operador Produção',
    },
    'LOGISTICA': {
        'label': 'Logística',
        'group_dn_env': 'AUTH_LDAP_GROUP_DN_LOGISTICA',
        'grupo_gestor': 'Gestor Logística',
        'grupo_colaborador': 'Operador Logística',
    },
}


class LdapIndisponivelError(Exception):
    """python-ldap não instalado ou conexão com o AD configurada incompleta."""


def _conectar():
    if not _LDAP_DISPONIVEL:
        raise LdapIndisponivelError('python-ldap não está instalado neste ambiente.')

    server_uri = getattr(settings, 'AUTH_LDAP_SERVER_URI', '')
    bind_dn = getattr(settings, 'AUTH_LDAP_BIND_DN', '')
    bind_password = getattr(settings, 'AUTH_LDAP_BIND_PASSWORD', '')
    if not (server_uri and bind_dn and bind_password):
        raise LdapIndisponivelError('Conexão com o AD não está configurada (.env).')

    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
    conn = ldap.initialize(server_uri)
    conn.set_option(ldap.OPT_REFERRALS, 0)
    conn.simple_bind_s(bind_dn, bind_password)
    return conn


def listar_membros_sem_usuario(area_key):
    """Retorna [{'username': sAMAccountName, 'nome': displayName}, ...] dos
    membros do grupo AD daquela área que ainda não têm User Django."""
    area = AREAS_AD[area_key]
    group_dn = getattr(settings, area['group_dn_env'], None) or __import__('os').environ.get(area['group_dn_env'])
    if not group_dn:
        raise LdapIndisponivelError(f"DN do grupo AD de {area['label']} não configurado no .env.")

    conn = _conectar()
    try:
        resultado = conn.search_s(group_dn, ldap.SCOPE_BASE, '(objectClass=group)', ['member'])
    finally:
        conn.unbind_s()

    if not resultado:
        return []

    _, attrs = resultado[0]
    membros_dn = [dn.decode('utf-8') if isinstance(dn, bytes) else dn for dn in attrs.get('member', [])]

    User = get_user_model()
    ja_cadastrados = set(
        User.objects.values_list('username', flat=True)
    )

    user_search_base = getattr(settings, 'AUTH_LDAP_USER_SEARCH_BASE', '')
    conn = _conectar()
    try:
        pendentes = []
        for membro_dn in membros_dn:
            resultado_usuario = conn.search_s(
                membro_dn, ldap.SCOPE_BASE, '(objectClass=user)', ['sAMAccountName', 'displayName'],
            )
            if not resultado_usuario:
                continue
            _, attrs_usuario = resultado_usuario[0]
            sam_bytes = attrs_usuario.get('sAMAccountName', [b''])[0]
            nome_bytes = attrs_usuario.get('displayName', [b''])[0]
            username = sam_bytes.decode('utf-8') if isinstance(sam_bytes, bytes) else sam_bytes
            nome = nome_bytes.decode('utf-8') if isinstance(nome_bytes, bytes) else nome_bytes
            if username and username not in ja_cadastrados:
                pendentes.append({'username': username, 'nome': nome or username})
    finally:
        conn.unbind_s()

    return sorted(pendentes, key=lambda p: p['nome'].lower())
