"""Busca de usuários do AD (todos, sem filtro por grupo) ainda sem usuário
Django — usado pela tela de atribuição manual (Administradores). Import do
python-ldap protegido pelo mesmo motivo de settings.py: sem wheel oficial
para Windows, o projeto deve continuar rodando sem essa dependência.
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model

try:
    import ldap
    from ldap.controls import SimplePagedResultsControl
    from ldap.filter import escape_filter_chars
    _LDAP_DISPONIVEL = True
except ImportError:
    _LDAP_DISPONIVEL = False

# Grupos Django que o Administrador pode atribuir a um usuário do AD.
GRUPOS_ATRIBUIVEIS = [
    'Gestor Produção',
    'Operador Produção',
    'Gestor Logística',
    'Operador Logística',
]

# Grupo (setor) e Subgrupo (papel) exibidos na tela de Usuários; o grupo Django
# é o subgrupo + grupo, ex.: "Gestor" + "Produção" -> "Gestor Produção".
SETORES = ['Produção', 'Logística']
PAPEIS = ['Gestor', 'Operador']


def nome_grupo_django(setor, papel):
    return f'{papel} {setor}'

TAMANHO_MINIMO_BUSCA = 2
TAMANHO_PAGINA_LISTAGEM = 500
LIMITE_RESULTADOS = 15
# Exclui contas desabilitadas (bit ACCOUNTDISABLE de userAccountControl).
_FILTRO_CONTA_ATIVA = '(!(userAccountControl:1.2.840.113556.1.4.803:=2))'


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


def _decodificar(valores):
    if not valores:
        return ''
    valor = valores[0]
    return valor.decode('utf-8') if isinstance(valor, bytes) else valor


def buscar_usuarios_ad(termo):
    """Retorna [{'username': sAMAccountName, 'nome': displayName}, ...] de
    usuários do AD cujo login ou nome contém `termo` e que ainda não têm
    User Django."""
    termo = (termo or '').strip()
    if len(termo) < TAMANHO_MINIMO_BUSCA:
        return []

    search_base = getattr(settings, 'AUTH_LDAP_USER_SEARCH_BASE', '') or os.environ.get('AUTH_LDAP_USER_SEARCH_BASE', '')
    if not search_base:
        raise LdapIndisponivelError('Base de busca de usuários do AD não configurada no .env.')

    padrao = escape_filter_chars(termo) if _LDAP_DISPONIVEL else termo
    filtro = (
        f'(&(objectCategory=person)(objectClass=user){_FILTRO_CONTA_ATIVA}'
        f'(|(sAMAccountName=*{padrao}*)(displayName=*{padrao}*)))'
    )

    conn = _conectar()
    try:
        try:
            resultado = conn.search_s(
                search_base, ldap.SCOPE_SUBTREE, filtro, ['sAMAccountName', 'displayName', 'mail'],
            )
        except ldap.SIZELIMIT_EXCEEDED:
            raise LdapIndisponivelError('Muitos resultados — digite mais letras para refinar a busca.')
    finally:
        conn.unbind_s()

    ja_cadastrados = {u.lower() for u in get_user_model().objects.values_list('username', flat=True)}

    encontrados = []
    for dn, attrs in resultado:
        if not dn:  # referrals do AD vêm com DN None
            continue
        username = _decodificar(attrs.get('sAMAccountName'))
        if not username or username.lower() in ja_cadastrados:
            continue
        encontrados.append({
            'username': username,
            'nome': _decodificar(attrs.get('displayName')) or username,
            'email': _decodificar(attrs.get('mail')),
        })

    encontrados.sort(key=lambda p: p['nome'].lower())
    return encontrados[:LIMITE_RESULTADOS]


def obter_usuario_ad(username):
    """Retorna {'username', 'nome', 'email'} do usuário ativo do AD com esse
    sAMAccountName exato, ou None se não existir. Usado para validar no servidor
    o usuário escolhido no autocomplete, sem depender do limite de resultados da
    busca por termo."""
    username = (username or '').strip()
    if not username:
        return None

    search_base = getattr(settings, 'AUTH_LDAP_USER_SEARCH_BASE', '') or os.environ.get('AUTH_LDAP_USER_SEARCH_BASE', '')
    if not search_base:
        raise LdapIndisponivelError('Base de busca de usuários do AD não configurada no .env.')

    padrao = escape_filter_chars(username) if _LDAP_DISPONIVEL else username
    filtro = (
        f'(&(objectCategory=person)(objectClass=user){_FILTRO_CONTA_ATIVA}'
        f'(sAMAccountName={padrao}))'
    )

    conn = _conectar()
    try:
        resultado = conn.search_s(
            search_base, ldap.SCOPE_SUBTREE, filtro, ['sAMAccountName', 'displayName', 'mail'],
        )
    finally:
        conn.unbind_s()

    for dn, attrs in resultado:
        if not dn:
            continue
        login = _decodificar(attrs.get('sAMAccountName'))
        if login:
            return {
                'username': login,
                'nome': _decodificar(attrs.get('displayName')) or login,
                'email': _decodificar(attrs.get('mail')),
            }
    return None


def _dns_grupos_producao_logistica():
    return [
        dn for dn in (
            os.environ.get('AUTH_LDAP_GROUP_DN_PRODUCAO', ''),
            os.environ.get('AUTH_LDAP_GROUP_DN_LOGISTICA', ''),
        ) if dn
    ]


def listar_usuarios_ad():
    """Retorna [{'username', 'nome'}, ...] com os usuários ativos do AD que
    pertencem aos grupos de Produção ou Logística (AUTH_LDAP_GROUP_DN_PRODUCAO/
    _LOGISTICA) e ainda não têm User Django, ordenados por nome. A busca é
    paginada (RFC 2696) para não esbarrar no limite de resultados do AD."""
    search_base = getattr(settings, 'AUTH_LDAP_USER_SEARCH_BASE', '') or os.environ.get('AUTH_LDAP_USER_SEARCH_BASE', '')
    if not search_base:
        raise LdapIndisponivelError('Base de busca de usuários do AD não configurada no .env.')

    dns_grupos = _dns_grupos_producao_logistica()
    if not dns_grupos:
        raise LdapIndisponivelError('Grupos de Produção/Logística do AD não configurados no .env.')
    membros = ''.join(f'(memberOf={escape_filter_chars(dn)})' for dn in dns_grupos)
    filtro = f'(&(objectCategory=person)(objectClass=user){_FILTRO_CONTA_ATIVA}(|{membros}))'
    controle = SimplePagedResultsControl(True, size=TAMANHO_PAGINA_LISTAGEM, cookie='')

    resultado = []
    conn = _conectar()
    try:
        while True:
            msgid = conn.search_ext(
                search_base, ldap.SCOPE_SUBTREE, filtro,
                ['sAMAccountName', 'displayName', 'mail'], serverctrls=[controle],
            )
            _, pagina, _, controles = conn.result3(msgid)
            resultado.extend(pagina)
            paginacao = [c for c in controles if c.controlType == SimplePagedResultsControl.controlType]
            if not paginacao or not paginacao[0].cookie:
                break
            controle.cookie = paginacao[0].cookie
    finally:
        conn.unbind_s()

    ja_cadastrados = {u.lower() for u in get_user_model().objects.values_list('username', flat=True)}

    encontrados = []
    for dn, attrs in resultado:
        if not dn:  # referrals do AD vêm com DN None
            continue
        username = _decodificar(attrs.get('sAMAccountName'))
        if not username or username.lower() in ja_cadastrados:
            continue
        encontrados.append({
            'username': username,
            'nome': _decodificar(attrs.get('displayName')) or username,
            'email': _decodificar(attrs.get('mail')),
        })

    encontrados.sort(key=lambda p: p['nome'].lower())
    return encontrados
