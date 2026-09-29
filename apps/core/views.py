from collections import Counter
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render

from apps.recebimento.models import Palete, PaleteHistorico, registrar_historico

from .constants import GRUPOS_INATIVOS_POR_PADRAO
from .decorators import group_required
from .forms import AtribuicaoUsuarioADForm, ReautenticacaoForm
from .ldap_lookup import AREAS_AD, LdapIndisponivelError, listar_membros_sem_usuario
from .models import PerfilUsuario

ITENS_POR_PAGINA_CONFIRMACAO = 20
TOP_N_PRODUTOS_GRAFICO = 15
PAPEL_PARA_GRUPO = {'GESTOR': 'grupo_gestor', 'COLABORADOR': 'grupo_colaborador'}


@login_required
def home(request):
    return render(request, 'core/home.html')


@group_required('Administradores', 'Operador Logística')
def confirmar_armazenagem(request):
    # Sempre a queryset completa (não paginada) — o .update() abaixo precisa afetar
    # todos os paletes ARMAZENADO, nunca só a página exibida no GET.
    paletes_armazenados = Palete.objects.filter(estado='ARMAZENADO').select_related('endereco').order_by('endereco__codigo')

    if request.method == 'POST':
        form = ReautenticacaoForm(request.POST)
        if form.is_valid() and request.user.check_password(form.cleaned_data['senha']):
            paletes_a_finalizar = list(paletes_armazenados)
            atualizados = paletes_armazenados.update(estado='FINALIZADO')
            registrar_historico(paletes_a_finalizar, 'FINALIZADO', usuario=request.user)
            messages.success(
                request,
                f'{atualizados} palete(s) confirmado(s) e sincronizado(s) com o SAP B1.',
            )
            return redirect('confirmar_armazenagem')
        messages.error(request, 'Senha inválida. Autorização não concedida.')
    else:
        form = ReautenticacaoForm()

    pagina_paletes = Paginator(paletes_armazenados, ITENS_POR_PAGINA_CONFIRMACAO).get_page(request.GET.get('page'))

    return render(request, 'core/confirmar_armazenagem.html', {
        'form': form,
        'paletes': pagina_paletes,
    })


@group_required('Administradores')
def atribuir_usuario_ad(request):
    """Lista membros dos grupos guarda-chuva do AD (Produção/Logística) que
    ainda não têm usuário no WMS e permite ao Administrador criar o usuário
    já definindo o papel (Gestor ou Colaborador) — necessário porque o AD
    ainda não distingue os dois papéis dentro do mesmo grupo (ver
    [TRANSIÇÃO] em wms/settings.py)."""
    area_selecionada = request.GET.get('area', 'PRODUCAO')
    if area_selecionada not in AREAS_AD:
        area_selecionada = 'PRODUCAO'

    if request.method == 'POST':
        form = AtribuicaoUsuarioADForm(request.POST)
        if form.is_valid():
            area = form.cleaned_data['area']
            username = form.cleaned_data['username']
            nome = form.cleaned_data['nome']
            papel = form.cleaned_data['papel']
            nome_grupo = AREAS_AD[area][PAPEL_PARA_GRUPO[papel]]

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
            return redirect(f"{request.path}?area={area}")
    else:
        form = AtribuicaoUsuarioADForm(initial={'area': area_selecionada})

    erro_ldap = None
    membros_pendentes = []
    try:
        membros_pendentes = listar_membros_sem_usuario(area_selecionada)
    except LdapIndisponivelError as exc:
        erro_ldap = str(exc)

    return render(request, 'core/atribuir_usuario_ad.html', {
        'form': form,
        'areas': AREAS_AD,
        'area_selecionada': area_selecionada,
        'membros_pendentes': membros_pendentes,
        'erro_ldap': erro_ldap,
    })


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
