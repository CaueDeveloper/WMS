from collections import Counter
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render

from apps.recebimento.models import Palete, PaleteHistorico, registrar_historico

from .decorators import group_required
from .forms import ReautenticacaoForm

ITENS_POR_PAGINA_CONFIRMACAO = 20
TOP_N_PRODUTOS_GRAFICO = 15


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
