"""Cadastro único das telas do sistema: alimenta o menu lateral e os atalhos
da tela inicial, com a regra de quem pode ver cada uma. Ao criar uma tela
nova, basta acrescentar um `ItemMenu` aqui; `secao` é o grupo (submenu) do
menu lateral: Produção, Logística, Consultar ou Usuários (vazio = item solto)."""
from dataclasses import dataclass

from django.urls import reverse

ADM = 'Administradores'
OP_PROD = 'Operador Produção'
OP_LOG = 'Operador Logística'
GESTOR_PROD = 'Gestor Produção'
GESTOR_LOG = 'Gestor Logística'

TODOS_OPERACIONAIS = (ADM, OP_PROD, OP_LOG, GESTOR_PROD, GESTOR_LOG)

PRODUCAO = 'Produção'
LOGISTICA = 'Logística'
CONSULTAR = 'Consultar'
USUARIOS = 'Usuários'
ICONES_SECAO = {PRODUCAO: 'tag', LOGISTICA: 'archive', CONSULTAR: 'search', USUARIOS: 'users'}


@dataclass(frozen=True)
class ItemMenu:
    rotulo: str
    icone: str
    view_name: str
    grupos: tuple
    secao: str = ''
    descricao: str = ''
    query: str = ''  # querystring que distingue telas com a mesma view (modo=lote, papel=gestor, tipo=motorista)
    relacionadas: tuple = ()  # telas filhas que mantêm este item destacado

    @property
    def url(self):
        base = reverse(self.view_name)
        return f'{base}?{self.query}' if self.query else base


ITENS_MENU = (
    ItemMenu('Início', 'home', 'home', (), secao=''),
    ItemMenu(
        'Produção', 'tag', 'producao:novo', (ADM, OP_PROD, GESTOR_PROD), secao=PRODUCAO,
        descricao='Emitir etiquetas e acompanhar os últimos paletes criados.',
    ),
    ItemMenu(
        'Estatísticas de Produção', 'chart-bar', 'producao:estatisticas', (ADM, GESTOR_PROD), secao=PRODUCAO,
        descricao='Ordens de produção dos últimos 30 dias e estoque disponível (SAP).',
    ),
    ItemMenu(
        'Transferência: Produção', 'transfer', 'producao:transferencia_gestor', (ADM, OP_PROD, GESTOR_PROD),
        secao=PRODUCAO, descricao='Bipar paletes e aprovar o lote para a Logística.',
        relacionadas=('producao:relatorio_aprovacao_producao',),
    ),
    ItemMenu(
        'Transferência: Logística', 'transfer', 'producao:transferencia_logistica', (ADM, OP_LOG, GESTOR_LOG),
        secao=LOGISTICA, descricao='Conferir o recebimento do lote na Logística.',
    ),
    ItemMenu(
        'Armazenagem', 'archive', 'logistica:putaway', (ADM, OP_LOG, GESTOR_LOG), secao=LOGISTICA,
        descricao='Guardar os paletes recebidos nos endereços livres.',
    ),
    ItemMenu(
        'Romaneio', 'archive', 'romaneio:lista', (ADM, OP_LOG, GESTOR_LOG), secao=LOGISTICA,
        descricao='Montar o romaneio de entrega com as retiradas de palete.',
        relacionadas=('romaneio:novo', 'romaneio:detalhe'),
    ),
    ItemMenu(
        'Etiquetas de corredor', 'tag', 'logistica:etiquetas_corredor', (ADM,), secao=LOGISTICA,
        descricao='Gerar e imprimir a etiqueta de cada corredor.',
    ),
    ItemMenu(
        'Mapa de Estoque', 'map', 'logistica:mapa_estoque', (ADM, GESTOR_PROD, GESTOR_LOG), secao=CONSULTAR,
        descricao='Ocupação por andar, corredor e guarda.',
        relacionadas=('logistica:inspecionar_palete',),
    ),
    ItemMenu(
        'Consultar palete', 'search', 'logistica:consultar_palete', TODOS_OPERACIONAIS, secao=CONSULTAR,
        descricao='Situação e histórico de um palete.', query='modo=palete',
    ),
    ItemMenu(
        'Consultar lote', 'search', 'logistica:consultar_palete', TODOS_OPERACIONAIS, secao=CONSULTAR,
        descricao='Todos os paletes de um lote.', query='modo=lote',
    ),
    ItemMenu(
        'Consultar corredor', 'pin', 'logistica:consultar_palete', TODOS_OPERACIONAIS, secao=CONSULTAR,
        descricao='Bipe o QR do corredor e veja a ocupação.', query='modo=corredor',
    ),
    ItemMenu(
        'Operadores', 'users', 'usuarios', (ADM,), secao=USUARIOS,
        descricao='Cadastrar operadores e definir perfis de acesso.', query='papel=operador',
    ),
    ItemMenu(
        'Gestores', 'users', 'usuarios', (ADM,), secao=USUARIOS,
        descricao='Cadastrar gestores e definir perfis de acesso.', query='papel=gestor',
    ),
    ItemMenu(
        'Estatísticas', 'chart-bar', 'estatisticas', (ADM, GESTOR_PROD, GESTOR_LOG), secao='',
        descricao='Volumes e produtividade por período.',
    ),
)


def grupos_do_usuario(user):
    return set(user.groups.values_list('name', flat=True))


def pode_ver(user, grupos, nomes_grupos=None):
    """Item sem `grupos` é de qualquer usuário logado; superusuário vê tudo."""
    if not grupos or user.is_superuser:
        return True
    nomes_grupos = grupos_do_usuario(user) if nomes_grupos is None else nomes_grupos
    return bool(nomes_grupos & set(grupos))


def itens_permitidos(user):
    nomes = grupos_do_usuario(user)
    return [item for item in ITENS_MENU if pode_ver(user, item.grupos, nomes)]


def _ativo(item, request):
    match = request.resolver_match
    if match is None:
        return False
    if match.view_name != item.view_name and match.view_name not in item.relacionadas:
        return False
    chave, _, valor = item.query.partition('=')
    return not item.query or request.GET.get(chave) == valor


def menu_para(request):
    """Menu lateral: itens soltos (Início) e grupos recolhíveis (Produção,
    Logística, Consultar, Usuários) só com o que o usuário pode abrir. O grupo da
    tela atual vem `aberta`; os demais ficam recolhidos."""
    secoes = {}
    for item in itens_permitidos(request.user):
        secao = secoes.setdefault(item.secao, {
            'titulo': item.secao, 'icone': ICONES_SECAO.get(item.secao, ''), 'itens': [], 'aberta': False,
        })
        ativo = _ativo(item, request)
        secao['aberta'] = secao['aberta'] or ativo
        secao['itens'].append({'rotulo': item.rotulo, 'icone': item.icone, 'url': item.url, 'ativo': ativo})
    return list(secoes.values())
