"""Cadastro único das telas do sistema: alimenta o menu lateral e os atalhos
da tela inicial, com a regra de quem pode ver cada uma. Ao criar uma tela
nova, basta acrescentar um `ItemMenu` aqui."""
from dataclasses import dataclass

from django.urls import reverse

ADM = 'Administradores'
OP_PROD = 'Operador Produção'
OP_LOG = 'Operador Logística'
GESTOR_PROD = 'Gestor Produção'
GESTOR_LOG = 'Gestor Logística'

TODOS_OPERACIONAIS = (ADM, OP_PROD, OP_LOG, GESTOR_PROD, GESTOR_LOG)


@dataclass(frozen=True)
class ItemMenu:
    rotulo: str
    icone: str
    view_name: str
    grupos: tuple
    secao: str = ''
    descricao: str = ''
    modo: str = ''  # só "Consultar": distingue Palete/Lote/Endereço pela querystring
    relacionadas: tuple = ()  # telas filhas que mantêm este item destacado

    @property
    def url(self):
        base = reverse(self.view_name)
        return f'{base}?modo={self.modo}' if self.modo else base


ITENS_MENU = (
    ItemMenu('Início', 'home', 'home', (), secao=''),
    ItemMenu(
        'Produção', 'tag', 'recebimento:novo', (ADM, OP_PROD), secao='Operação',
        descricao='Emitir etiquetas e acompanhar os últimos paletes criados.',
    ),
    ItemMenu(
        'Transferência: Produção', 'transfer', 'recebimento:transferencia_gestor', (ADM, OP_PROD, GESTOR_PROD),
        secao='Operação', descricao='Bipar paletes e aprovar o lote para a Logística.',
        relacionadas=('recebimento:relatorio_aprovacao_producao',),
    ),
    ItemMenu(
        'Transferência: Logística', 'transfer', 'recebimento:transferencia_logistica', (ADM, OP_LOG, GESTOR_LOG),
        secao='Operação', descricao='Conferir o recebimento do lote na Logística.',
    ),
    ItemMenu(
        'Armazenagem', 'archive', 'estoque:putaway', (ADM, OP_LOG), secao='Operação',
        descricao='Guardar os paletes recebidos nos endereços livres.',
    ),
    ItemMenu(
        'Plano de Armazenagem', 'pin', 'estoque:plano_armazenagem', (ADM, OP_LOG), secao='Operação',
        descricao='Ver o caminho até o endereço de um palete.',
    ),
    ItemMenu(
        'Mapa de Estoque', 'map', 'estoque:mapa_estoque', (ADM, GESTOR_PROD, GESTOR_LOG), secao='Consulta',
        descricao='Ocupação por andar, corredor e guarda.',
        relacionadas=('estoque:inspecionar_palete',),
    ),
    ItemMenu(
        'Consultar palete', 'search', 'estoque:consultar_palete', TODOS_OPERACIONAIS, secao='Consulta',
        descricao='Situação e histórico de um palete.', modo='palete',
    ),
    ItemMenu(
        'Consultar lote', 'search', 'estoque:consultar_palete', TODOS_OPERACIONAIS, secao='Consulta',
        descricao='Todos os paletes de um lote.', modo='lote',
    ),
    ItemMenu(
        'Estatísticas', 'chart-bar', 'estatisticas', (ADM, GESTOR_PROD, GESTOR_LOG), secao='Consulta',
        descricao='Volumes e produtividade por período.',
    ),
    ItemMenu(
        'Usuários', 'users', 'usuarios', (ADM,), secao='Administração',
        descricao='Cadastrar usuários e definir perfis de acesso.',
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
    return not item.modo or request.GET.get('modo') == item.modo


def menu_para(request):
    """Menu lateral agrupado por seção, só com o que o usuário pode abrir."""
    secoes = []
    for item in itens_permitidos(request.user):
        if not secoes or secoes[-1]['titulo'] != item.secao:
            secoes.append({'titulo': item.secao, 'itens': []})
        secoes[-1]['itens'].append({
            'rotulo': item.rotulo, 'icone': item.icone, 'url': item.url, 'ativo': _ativo(item, request),
        })
    return secoes
