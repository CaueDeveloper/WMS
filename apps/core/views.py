from collections import Counter
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import redirect, render

from apps.recebimento.models import Palete, PaleteHistorico

from .constants import GRUPOS_INATIVOS_POR_PADRAO
from .decorators import group_required
from .forms import AtribuicaoUsuarioADForm
from .ldap_lookup import LdapIndisponivelError, buscar_usuarios_ad
from .models import PerfilUsuario

TOP_N_PRODUTOS_GRAFICO = 15


@login_required
def home(request):
    return render(request, 'core/home.html')


@group_required('Administradores')
def buscar_usuarios_ad_json(request):
    """Autocomplete da tela de atribuição: usuários do AD (todos) que casam
    com `?q=` e ainda não têm usuário no WMS."""
    try:
        usuarios = buscar_usuarios_ad(request.GET.get('q', ''))
    except LdapIndisponivelError as exc:
        return JsonResponse({'erro': str(exc), 'usuarios': []})
    return JsonResponse({'usuarios': usuarios})


@group_required('Administradores')
def atribuir_usuario_ad(request):
    """Permite ao Administrador buscar qualquer usuário do AD (digitando) e
    criar o usuário no WMS já definindo o grupo — necessário porque o AD
    ainda não distingue os papéis (ver [TRANSIÇÃO] em wms/settings.py)."""
    if request.method == 'POST':
        form = AtribuicaoUsuarioADForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            nome = form.cleaned_data['nome']
            nome_grupo = form.cleaned_data['grupo']

            User = get_user_model()
            if User.objects.filter(username=username).exists():
                messages.error(request, f'O usuário "{username}" já existe no WMS.')
            else:
                partes_nome = nome.split(' ', 1)
                usuario = User.objects.create_user(
                    username=username,
                    first_name=partes_nome[0],
                    last_name=partes_nome[1] if len(partes_nome) > 1 else '',
                )
                usuario.set_unusable_password()  # autenticação continua via LDAP
                # Nasce inativo — Produção/Logística exigem ativação manual por um
                # Administrador (tela "Ativar Usuários") antes de conseguir logar.
                usuario.is_active = False
                usuario.save()
                PerfilUsuario.objects.create(user=usuario, atribuido_manualmente=True)
                grupo = Group.objects.get(name=nome_grupo)
                usuario.groups.add(grupo)
                messages.success(
                    request,
                    f'"{nome}" atribuído como {nome_grupo}. Usuário criado inativo — '
                    f'ative-o em "Ativar Usuários" antes que ele possa acessar o sistema.',
                )
            return redirect(request.path)
    else:
        form = AtribuicaoUsuarioADForm()

    return render(request, 'core/atribuir_usuario_ad.html', {'form': form})


@group_required('Administradores')
def ativar_usuarios(request):
    """Lista usuários dos grupos de Produção/Logística que nasceram inativos
    (criados manualmente ou via primeiro login LDAP, ver GRUPOS_INATIVOS_POR_PADRAO
    em ldap_signals.py) e permite a um Administrador ativá-los, liberando o
    acesso ao WMS."""
    User = get_user_model()

    if request.method == 'POST':
        usuario = User.objects.filter(pk=request.POST.get('usuario_id'), is_active=False).first()
        if usuario:
            usuario.is_active = True
            usuario.save(update_fields=['is_active'])
            messages.success(request, f'"{usuario.get_full_name() or usuario.username}" foi ativado e já pode acessar o WMS.')
        return redirect('ativar_usuarios')

    usuarios_pendentes = (
        User.objects.filter(is_active=False, groups__name__in=GRUPOS_INATIVOS_POR_PADRAO)
        .distinct()
        .prefetch_related('groups')
        .order_by('first_name', 'last_name', 'username')
    )

    return render(request, 'core/ativar_usuarios.html', {
        'usuarios_pendentes': usuarios_pendentes,
    })


def _parse_data(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _montar_datasets_por_sku(dias_ordenados, skus_ordenados, contagem_por_dia_sku):
    """Monta uma lista de datasets (um por SKU) no formato aceito pelo
    Chart.js para grafico de barras empilhadas, a partir de uma contagem
    indexada por (dia, sku)."""
    rotulos = [d.isoformat() for d in dias_ordenados]
    datasets = []
    for sku in skus_ordenados:
        valores = [contagem_por_dia_sku.get((dia, sku), 0) for dia in dias_ordenados]
        datasets.append({'sku': sku, 'valores': valores})
    return rotulos, datasets


def _serie_por_dia_e_sku(queryset_anotado_dia):
    """Conta paletes por dia, quebrado por SKU: retorna (rotulos, datasets),
    onde cada dataset e {'sku': ..., 'valores': [...]} alinhado aos rotulos."""
    linhas = list(
        queryset_anotado_dia.values('dia', 'sku')
        .annotate(quantidade=Count('id'))
        .order_by('dia')
    )
    dias_ordenados = sorted({item['dia'] for item in linhas})
    skus_ordenados = sorted({item['sku'] for item in linhas})
    contagem = {(item['dia'], item['sku']): item['quantidade'] for item in linhas}
    return _montar_datasets_por_sku(dias_ordenados, skus_ordenados, contagem)


def _serie_lotes_por_dia_e_sku(historico_queryset):
    """Conta lotes DISTINTOS aprovados por dia a partir de um queryset de
    PaleteHistorico ja filtrado por estado+periodo, quebrado por SKU do
    palete. Agrega em Python porque contar distinct(dia, lote) agrupado por
    dia nao e trivial de forma portatil entre SQLite/MySQL. Um lote pode (em
    tese) misturar SKUs diferentes, entao cada combinacao (dia, lote, sku) e
    contada como parte da serie daquele SKU."""
    pares = (
        historico_queryset.annotate(dia=TruncDate('criado_em'))
        .values('dia', 'palete__lote', 'palete__sku')
        .distinct()
    )
    contagem = Counter((item['dia'], item['palete__sku']) for item in pares)
    dias_ordenados = sorted({dia for dia, _sku in contagem.keys()})
    skus_ordenados = sorted({sku for _dia, sku in contagem.keys()})
    return _montar_datasets_por_sku(dias_ordenados, skus_ordenados, contagem)


@login_required
def estatisticas(request):
    pode_ver_producao = request.user.is_superuser or request.user.groups.filter(
        name__in=['Administradores', 'Gestor Produção']
    ).exists()
    pode_ver_logistica = request.user.is_superuser or request.user.groups.filter(
        name__in=['Administradores', 'Gestor Logística']
    ).exists()

    criados_de = _parse_data(request.GET.get('criados_de'))
    criados_ate = _parse_data(request.GET.get('criados_ate'))
    criados_sku = request.GET.get('criados_sku', '')
    producao_de = _parse_data(request.GET.get('producao_de'))
    producao_ate = _parse_data(request.GET.get('producao_ate'))
    producao_sku = request.GET.get('producao_sku', '')
    logistica_de = _parse_data(request.GET.get('logistica_de'))
    logistica_ate = _parse_data(request.GET.get('logistica_ate'))
    logistica_sku = request.GET.get('logistica_sku', '')

    produtos_disponiveis = list(
        Palete.objects.values('sku', 'descricao').distinct().order_by('sku')
    )

    paletes_criados_qs = Palete.objects.all()
    if criados_de:
        paletes_criados_qs = paletes_criados_qs.filter(criado_em__date__gte=criados_de)
    if criados_ate:
        paletes_criados_qs = paletes_criados_qs.filter(criado_em__date__lte=criados_ate)
    if criados_sku:
        paletes_criados_qs = paletes_criados_qs.filter(sku=criados_sku)

    total_paletes_criados = paletes_criados_qs.count()

    criados_labels, criados_datasets = _serie_por_dia_e_sku(
        paletes_criados_qs.annotate(dia=TruncDate('criado_em'))
    )

    por_produto = list(
        paletes_criados_qs.values('sku', 'descricao')
        .annotate(quantidade=Count('id'), peso_total=Sum('peso_kg'))
        .order_by('-quantidade')
    )
    top_produtos = por_produto[:TOP_N_PRODUTOS_GRAFICO]
    produto_labels = [item['sku'] for item in top_produtos]
    produto_quantidades = [item['quantidade'] for item in top_produtos]
    produto_pesos = [float(item['peso_total']) for item in top_produtos]

    total_lotes_producao = 0
    peso_total_producao = 0
    producao_labels, producao_datasets = [], []
    if pode_ver_producao:
        hist_producao = PaleteHistorico.objects.filter(estado='CONFERIDO')
        if producao_de:
            hist_producao = hist_producao.filter(criado_em__date__gte=producao_de)
        if producao_ate:
            hist_producao = hist_producao.filter(criado_em__date__lte=producao_ate)
        if producao_sku:
            hist_producao = hist_producao.filter(palete__sku=producao_sku)

        total_lotes_producao = hist_producao.values('palete__lote').distinct().count()
        peso_total_producao = Palete.objects.filter(
            pk__in=hist_producao.values('palete_id')
        ).aggregate(total=Sum('peso_kg'))['total'] or 0

        producao_labels, producao_datasets = _serie_lotes_por_dia_e_sku(hist_producao)

    total_lotes_logistica = 0
    peso_total_logistica = 0
    logistica_labels, logistica_datasets = [], []
    if pode_ver_logistica:
        hist_logistica = PaleteHistorico.objects.filter(estado='CONFERIDO_LOGISTICA')
        if logistica_de:
            hist_logistica = hist_logistica.filter(criado_em__date__gte=logistica_de)
        if logistica_ate:
            hist_logistica = hist_logistica.filter(criado_em__date__lte=logistica_ate)
        if logistica_sku:
            hist_logistica = hist_logistica.filter(palete__sku=logistica_sku)

        total_lotes_logistica = hist_logistica.values('palete__lote').distinct().count()
        peso_total_logistica = Palete.objects.filter(
            pk__in=hist_logistica.values('palete_id')
        ).aggregate(total=Sum('peso_kg'))['total'] or 0

        logistica_labels, logistica_datasets = _serie_lotes_por_dia_e_sku(hist_logistica)

    template = 'core/estatisticas.html'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        template = 'core/_resultados_estatisticas.html'

    return render(request, template, {
        'pode_ver_producao': pode_ver_producao,
        'pode_ver_logistica': pode_ver_logistica,
        'criados_de': request.GET.get('criados_de', ''),
        'criados_ate': request.GET.get('criados_ate', ''),
        'criados_sku': criados_sku,
        'produtos_disponiveis': produtos_disponiveis,
        'producao_de': request.GET.get('producao_de', ''),
        'producao_ate': request.GET.get('producao_ate', ''),
        'producao_sku': producao_sku,
        'logistica_de': request.GET.get('logistica_de', ''),
        'logistica_ate': request.GET.get('logistica_ate', ''),
        'logistica_sku': logistica_sku,

        'total_paletes_criados': total_paletes_criados,
        'por_produto': por_produto,

        'total_lotes_producao': total_lotes_producao,
        'peso_total_producao': peso_total_producao,

        'total_lotes_logistica': total_lotes_logistica,
        'peso_total_logistica': peso_total_logistica,

        'grafico_criados': {'labels': criados_labels, 'datasets': criados_datasets},
        'grafico_produto': {
            'labels': produto_labels, 'quantidades': produto_quantidades, 'pesos': produto_pesos,
        },
        'grafico_producao': {'labels': producao_labels, 'datasets': producao_datasets},
        'grafico_logistica': {'labels': logistica_labels, 'datasets': logistica_datasets},
    })
