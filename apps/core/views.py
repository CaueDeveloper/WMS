from collections import Counter

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.producao.models import Palete, PaleteHistorico

from .decorators import group_required
from .forms import AlterarGrupoUsuarioForm, NovoOperadorForm, NovoUsuarioForm
from .ldap_lookup import (
    GRUPOS_ATRIBUIVEIS, PAPEIS, SETORES, LdapIndisponivelError, buscar_usuarios_ad, listar_usuarios_ad, nome_grupo_django,
    obter_usuario_ad,
)
from .models import PerfilUsuario
from .navegacao import itens_permitidos

TOP_N_PRODUTOS_GRAFICO = 15


# Etapas do palete na tela inicial, na ordem do fluxo real. `tela` é a view que
# resolve aquela etapa; o link só aparece se o usuário tiver acesso a ela.
ETAPAS_FLUXO = (
    ('Fabricados', ('FABRICADO',), 'producao:transferencia_gestor'),
    ('Transferência de Produção', ('TRANSFERENCIA_PRODUCAO',), 'producao:transferencia_gestor'),
    ('Transferência de Logística', ('APROVADO_PRODUCAO', 'TRANSFERENCIA_LOGISTICA'), 'producao:transferencia_logistica'),
    ('A armazenar', ('APROVADO_LOGISTICA',), 'logistica:putaway'),
    ('Armazenados', ('ARMAZENADO',), 'logistica:mapa_estoque'),
)
MOVIMENTACOES_RECENTES = 8
DIAS_SEMANA = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')
MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')


@login_required
def home(request):
    permitidos = itens_permitidos(request.user)
    telas_liberadas = {item.view_name for item in permitidos}
    por_estado = dict(Palete.objects.values_list('estado').annotate(n=Count('id')))

    etapas = []
    for rotulo, estados, tela in ETAPAS_FLUXO:
        etapas.append({
            'rotulo': rotulo,
            'quantidade': sum(por_estado.get(estado, 0) for estado in estados),
            'url': reverse(tela) if tela in telas_liberadas else None,
        })

    hoje = timezone.localdate()
    return render(request, 'core/home.html', {
        'hoje': f'{DIAS_SEMANA[hoje.weekday()].capitalize()}, {hoje.day} de {MESES[hoje.month - 1]}',
        'etapas': etapas,
        'atalhos': [item for item in permitidos if item.view_name != 'home' and item.descricao],
        # Usuário do AD ainda sem perfil não vê movimentações (barcodes, nomes).
        'recentes': (
            PaleteHistorico.objects.select_related('palete', 'usuario').prefetch_related('usuario__groups').order_by('-criado_em')[:MOVIMENTACOES_RECENTES]
            if request.user.is_superuser or request.user.groups.exists() else []
        ),
    })


@group_required('Administradores')
def buscar_usuarios_ad_json(request):
    """Lista do pop-up "Novo": sem `?q=`, os usuários de Produção/Logística do
    AD ainda sem usuário no WMS; com `?q=`, busca em todo o AD."""
    try:
        termo = request.GET.get('q', '').strip()
        usuarios = buscar_usuarios_ad(termo) if termo else listar_usuarios_ad()
    except LdapIndisponivelError as exc:
        return JsonResponse({'erro': str(exc), 'usuarios': []})
    except Exception:
        return JsonResponse({'erro': 'Falha ao consultar o AD.', 'usuarios': []})
    return JsonResponse({'usuarios': usuarios})


def _grupo_e_subgrupo(usuario):
    """Separa o grupo Django de Produção/Logística do usuário em (setor, papel),
    ex.: "Gestor Produção" -> ("Produção", "Gestor"). Usuário sem grupo
    atribuível volta ("—", "—")."""
    nomes = {g.name for g in usuario.groups.all()}
    for setor in SETORES:
        for papel in PAPEIS:
            if nome_grupo_django(setor, papel) in nomes:
                return setor, papel
    return '—', '—'


def _aplicar_grupo(usuario, setor, papel):
    """Troca o papel do usuário: remove os grupos de Produção/Logística atuais e
    aplica o novo. atribuido_manualmente impede o login LDAP de sobrescrevê-lo."""
    usuario.groups.remove(*Group.objects.filter(name__in=GRUPOS_ATRIBUIVEIS))
    usuario.groups.add(Group.objects.get(name=nome_grupo_django(setor, papel)))
    PerfilUsuario.objects.update_or_create(user=usuario, defaults={'atribuido_manualmente': True})


def _usuarios_gerenciaveis():
    User = get_user_model()
    return (
        User.objects.filter(is_superuser=False)
        .exclude(groups__name='Administradores')
        .prefetch_related('groups')
        .order_by('first_name', 'last_name', 'username')
    )


def _usuario_gerenciavel_por_pk(pk):
    return _usuarios_gerenciaveis().filter(pk=pk).first() if str(pk).isdigit() else None


PAPEIS_DA_LISTA = {'operador': 'Operador', 'gestor': 'Gestor'}  # ?papel= da tela Usuários -> subgrupo


@group_required('Administradores')
def usuarios(request):
    """Lista os usuários do WMS (Nome, Email, Grupo, Subgrupo) e trata, via POST,
    as ações do Administrador: `novo` (pop-up que busca o usuário no AD e o
    atribui a um grupo), `alterar_grupo` e `excluir`. A atribuição manual é
    necessária porque o AD ainda não distingue os papéis (ver [TRANSIÇÃO] em
    wms/settings.py)."""
    User = get_user_model()
    filtro = PAPEIS_DA_LISTA.get(request.GET.get('papel') or request.POST.get('papel'))
    redirecionar = redirect(f"{reverse('usuarios')}?papel={filtro.lower()}" if filtro else 'usuarios')

    if request.method == 'POST':
        acao = request.POST.get('acao')

        if acao == 'novo':
            form = NovoUsuarioForm(request.POST)
            if not form.is_valid():
                messages.error(request, 'Selecione um usuário do AD, o grupo e o subgrupo.')
                return redirecionar
            username = form.cleaned_data['username']
            try:
                usuario_ad = obter_usuario_ad(username)
            except LdapIndisponivelError as exc:
                messages.error(request, str(exc))
                return redirecionar
            except Exception:
                messages.error(request, 'Falha ao consultar o AD.')
                return redirecionar

            if usuario_ad is None:
                messages.error(request, f'Usuário "{username}" não encontrado no AD (ou conta desabilitada).')
            elif User.objects.filter(username__iexact=usuario_ad['username']).exists():
                messages.error(request, f'O usuário "{usuario_ad["username"]}" já existe no WMS.')
            else:
                setor, papel = form.cleaned_data['grupo'], form.cleaned_data['subgrupo']
                nome = usuario_ad['nome']
                partes_nome = nome.split(' ', 1)
                usuario = User.objects.create_user(
                    username=usuario_ad['username'],
                    email=usuario_ad['email'],
                    first_name=partes_nome[0],
                    last_name=partes_nome[1] if len(partes_nome) > 1 else '',
                )
                usuario.set_unusable_password()  # autenticação continua via LDAP
                # Nasce inativo — Produção/Logística exigem ativação manual por um
                # Administrador (ação "Ativar" na tela Usuários) antes de conseguir logar.
                usuario.is_active = False
                usuario.save()
                _aplicar_grupo(usuario, setor, papel)
                messages.success(
                    request,
                    f'"{nome}" cadastrado como {nome_grupo_django(setor, papel)}. '
                    f'Usuário criado inativo — ative-o na lista abaixo antes que ele possa acessar o sistema.',
                )
            return redirecionar

        if acao == 'novo_operador':
            # Operador não está no AD: o Administrador cria login e senha locais.
            form = NovoOperadorForm(request.POST)
            if not form.is_valid():
                messages.error(request, 'Informe nome, usuário, senha (mínimo 6 caracteres) e grupo.')
            elif User.objects.filter(username__iexact=form.cleaned_data['username'].strip()).exists():
                messages.error(request, f'O usuário "{form.cleaned_data["username"].strip()}" já existe no WMS.')
            else:
                dados = form.cleaned_data
                partes_nome = dados['nome'].strip().split(' ', 1)
                usuario = User.objects.create_user(
                    username=dados['username'].strip(), password=dados['senha'],
                    first_name=partes_nome[0], last_name=partes_nome[1] if len(partes_nome) > 1 else '',
                )
                _aplicar_grupo(usuario, dados['grupo'], 'Operador')
                messages.success(request, f'"{dados["nome"].strip()}" cadastrado como {nome_grupo_django(dados["grupo"], "Operador")}.')
            return redirecionar

        if acao == 'alterar_grupo':
            form = AlterarGrupoUsuarioForm(request.POST)
            usuario = _usuario_gerenciavel_por_pk(request.POST.get('usuario_id'))
            if not form.is_valid() or usuario is None:
                messages.error(request, 'Não foi possível alterar o grupo: dados inválidos.')
                return redirecionar
            setor, papel = form.cleaned_data['grupo'], form.cleaned_data['subgrupo']
            _aplicar_grupo(usuario, setor, papel)
            messages.success(
                request,
                f'"{usuario.get_full_name() or usuario.username}" agora é {nome_grupo_django(setor, papel)}. '
                f'O menu e as telas dele já refletem o novo grupo no próximo acesso.',
            )
            return redirecionar

        if acao == 'ativar':
            usuario = _usuario_gerenciavel_por_pk(request.POST.get('usuario_id'))
            if usuario is None or usuario.is_active:
                messages.error(request, 'Usuário não encontrado ou já ativo.')
            else:
                usuario.is_active = True
                usuario.save(update_fields=['is_active'])
                messages.success(request, f'"{usuario.get_full_name() or usuario.username}" foi ativado e já pode acessar o WMS.')
            return redirecionar

        if acao == 'inativar':
            usuario = _usuario_gerenciavel_por_pk(request.POST.get('usuario_id'))
            if usuario is None or not usuario.is_active:
                messages.error(request, 'Usuário não encontrado ou já inativo.')
            else:
                usuario.is_active = False
                usuario.save(update_fields=['is_active'])
                messages.success(request, f'"{usuario.get_full_name() or usuario.username}" foi inativado e não consegue mais acessar o WMS.')
            return redirecionar

        if acao == 'excluir':
            usuario = _usuario_gerenciavel_por_pk(request.POST.get('usuario_id'))
            if usuario is None:
                messages.error(request, 'Usuário não encontrado.')
            else:
                nome = usuario.get_full_name() or usuario.username
                usuario.delete()
                messages.success(request, f'Usuário "{nome}" excluído.')
            return redirecionar

        return redirecionar

    linhas = []
    for usuario in _usuarios_gerenciaveis():
        setor, subgrupo = _grupo_e_subgrupo(usuario)
        if filtro is None or subgrupo == filtro:
            linhas.append({'usuario': usuario, 'grupo': setor, 'subgrupo': subgrupo})

    return render(request, 'core/usuarios.html', {
        'linhas': linhas,
        'papel': filtro,
        'titulo': f'{filtro}es' if filtro else 'Usuários',
        'form_novo': NovoUsuarioForm(),
        'form_operador': NovoOperadorForm(),
        'form_alterar': AlterarGrupoUsuarioForm(),
    })


TOP_N_SKUS_POR_DIA = 5


def _rotulos_e_datasets_por_sku(dias_ordenados, skus_ordenados, contagem_por_dia_sku):
    """Monta (rotulos, datasets) no formato do gráfico de barras empilhadas:
    um dataset {'sku', 'valores'} por SKU, alinhado aos dias."""
    rotulos = [d.isoformat() for d in dias_ordenados]
    datasets = []
    for sku in skus_ordenados:
        valores = [contagem_por_dia_sku.get((dia, sku), 0) for dia in dias_ordenados]
        datasets.append({'sku': sku, 'valores': valores})
    return rotulos, datasets


def _serie_criados_por_dia_top_skus(queryset_anotado_dia):
    """Paletes criados por dia, quebrado pelos TOP_N_SKUS_POR_DIA SKUs de maior
    volume; os demais SKUs são somados em "Outros" — a pilha de cada dia
    continua igual ao total do dia, sem estourar a paleta de cores."""
    linhas = list(
        queryset_anotado_dia.values('dia', 'sku').annotate(quantidade=Count('id'))
    )
    total_por_sku = Counter()
    for item in linhas:
        total_por_sku[item['sku']] += item['quantidade']
    principais = [sku for sku, _ in total_por_sku.most_common(TOP_N_SKUS_POR_DIA)]

    contagem = Counter()
    tem_outros = False
    for item in linhas:
        sku = item['sku'] if item['sku'] in principais else 'Outros'
        tem_outros = tem_outros or sku == 'Outros'
        contagem[(item['dia'], sku)] += item['quantidade']

    dias_ordenados = sorted({item['dia'] for item in linhas})
    skus_ordenados = principais + (['Outros'] if tem_outros else [])
    return _rotulos_e_datasets_por_sku(dias_ordenados, skus_ordenados, contagem)


def _paletes_por_dia(queryset):
    """{dia: n} a partir de um queryset já agrupado por `dia` com `n` anotado."""
    return {item['dia']: item['n'] for item in queryset}


def _serie_fluxo_diario(pode_ver_producao, pode_ver_logistica):
    """Paletes por dia em cada etapa do fluxo: criados, aprovados pela
    Produção (APROVADO_PRODUCAO) e recebidos pela Logística (APROVADO_LOGISTICA).
    A mesma unidade (paletes) nas três séries permite comparar o funil."""
    criados = _paletes_por_dia(
        Palete.objects.annotate(dia=TruncDate('criado_em')).values('dia').annotate(n=Count('id'))
    )
    producao = logistica = None
    if pode_ver_producao:
        producao = _paletes_por_dia(
            PaleteHistorico.objects.filter(estado='APROVADO_PRODUCAO').annotate(dia=TruncDate('criado_em'))
            .values('dia').annotate(n=Count('palete_id', distinct=True))
        )
    if pode_ver_logistica:
        logistica = _paletes_por_dia(
            PaleteHistorico.objects.filter(estado='APROVADO_LOGISTICA').annotate(dia=TruncDate('criado_em'))
            .values('dia').annotate(n=Count('palete_id', distinct=True))
        )

    dias = set(criados)
    for serie in (producao, logistica):
        if serie:
            dias |= set(serie)
    dias_ordenados = sorted(dias)

    def valores(serie):
        return None if serie is None else [serie.get(dia, 0) for dia in dias_ordenados]

    return {
        'labels': [d.isoformat() for d in dias_ordenados],
        'criados': valores(criados),
        'producao': valores(producao),
        'logistica': valores(logistica),
    }


def _totais_lotes_aprovados(estado):
    """(lotes distintos aprovados, peso total em kg) dos paletes que chegaram ao `estado`."""
    historico = PaleteHistorico.objects.filter(estado=estado)
    lotes = historico.values('palete__lote').distinct().count()
    peso = Palete.objects.filter(pk__in=historico.values('palete_id')).aggregate(total=Sum('peso_kg'))['total'] or 0
    return lotes, peso


@group_required('Administradores', 'Gestor Produção', 'Gestor Logística')
def estatisticas(request):
    pode_ver_producao = request.user.is_superuser or request.user.groups.filter(
        name__in=['Administradores', 'Gestor Produção']
    ).exists()
    pode_ver_logistica = request.user.is_superuser or request.user.groups.filter(
        name__in=['Administradores', 'Gestor Logística']
    ).exists()

    paletes = Palete.objects.all()
    total_paletes_criados = paletes.count()
    criados_labels, criados_datasets = _serie_criados_por_dia_top_skus(
        paletes.annotate(dia=TruncDate('criado_em'))
    )

    por_produto = list(
        paletes.values('sku', 'descricao')
        .annotate(quantidade=Count('id'), peso_total=Sum('peso_kg'))
        .order_by('-quantidade')
    )
    top_produtos = por_produto[:TOP_N_PRODUTOS_GRAFICO]

    total_lotes_producao = peso_total_producao = 0
    if pode_ver_producao:
        total_lotes_producao, peso_total_producao = _totais_lotes_aprovados('APROVADO_PRODUCAO')

    total_lotes_logistica = peso_total_logistica = 0
    if pode_ver_logistica:
        total_lotes_logistica, peso_total_logistica = _totais_lotes_aprovados('APROVADO_LOGISTICA')

    template = 'core/estatisticas.html'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        template = 'core/_resultados_estatisticas.html'

    return render(request, template, {
        'pode_ver_producao': pode_ver_producao,
        'pode_ver_logistica': pode_ver_logistica,
        'total_paletes_criados': total_paletes_criados,
        'por_produto': por_produto,
        'total_lotes_producao': total_lotes_producao,
        'peso_total_producao': peso_total_producao,
        'total_lotes_logistica': total_lotes_logistica,
        'peso_total_logistica': peso_total_logistica,
        'dados': {
            'fluxo': _serie_fluxo_diario(pode_ver_producao, pode_ver_logistica),
            'criados': {'labels': criados_labels, 'datasets': criados_datasets},
            'produto': {
                'labels': [f"{item['sku']} — {item['descricao']}" for item in top_produtos],
                'quantidades': [item['quantidade'] for item in top_produtos],
                'pesos': [float(item['peso_total']) for item in top_produtos],
            },
        },
    })
