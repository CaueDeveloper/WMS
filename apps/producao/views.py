import itertools
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import group_required
from apps.logistica.models import Produto

from .forms import CorrecaoPaleteForm, RecebimentoForm
from .models import EstoqueItemSAP, OrdemProducao, Palete, PaleteHistorico, registrar_historico
from .services import (
    LoteIncompletoError, LoteVazioError, PaleteStateError, aprovar_lote, aprovar_lote_logistica,
    corrigir_palete, reprovar_palete, sincronizar_sap,
)
from .sap import SapIndisponivelError
from .etiqueta_preview import BARCODE_EXEMPLO, renderizar_preview
from .zpl import disparar_impressao_async

PESO_FIXO_RECEBIMENTO_KG = Decimal('500')
GRUPOS_POR_PAGINA_TRANSFERENCIA = 5
SEP_CHAVE = '|'  # separa lote e SKU na chave de um grupo (aprovação e relatório são por produto)
PALETES_RECENTES_NA_TELA = 20


@group_required('Administradores', 'Operador Produção', 'Gestor Produção')
@require_POST
def previsualizar_etiqueta(request):
    """PNG da etiqueta com os dados do formulário, sem criar nada."""
    form = RecebimentoForm(request.POST)
    if not form.is_valid():
        return HttpResponse('Dados inválidos.', status=400, content_type='text/plain; charset=utf-8')
    dados = form.cleaned_data
    peso_kg = PESO_FIXO_RECEBIMENTO_KG
    if dados['peso_variavel'] and dados['quantidade'] == 1:
        peso_kg = dados['peso_ultimo']
    produto = dados['produto']
    palete = Palete(
        sku=produto.sku, descricao=produto.descricao, lote=dados['lote'], peso_kg=peso_kg,
        data_fabricacao=dados['data_fabricacao'], data_vencimento=dados['data_vencimento'],
        barcode_palete=BARCODE_EXEMPLO,
    )
    png = renderizar_preview(palete)
    resposta = HttpResponse(png, content_type='image/png')
    resposta['Cache-Control'] = 'no-store'
    return resposta


@group_required('Administradores', 'Operador Produção', 'Gestor Produção')
def producao(request):
    if request.method == 'POST':
        form = RecebimentoForm(request.POST)
        if form.is_valid():
            produto = form.cleaned_data['produto']
            lote = form.cleaned_data['lote']
            quantidade = form.cleaned_data['quantidade']
            data_fabricacao = form.cleaned_data['data_fabricacao']
            data_vencimento = form.cleaned_data['data_vencimento']
            pesos = [PESO_FIXO_RECEBIMENTO_KG] * quantidade
            if form.cleaned_data['peso_variavel']:
                pesos[-1] = form.cleaned_data['peso_ultimo']  # só o último palete tem peso próprio

            paletes = [
                Palete(
                    sku=produto.sku, descricao=produto.descricao, lote=lote,
                    peso_kg=peso_kg, data_fabricacao=data_fabricacao, data_vencimento=data_vencimento,
                )
                for peso_kg in pesos
            ]
            # save() por palete (e não bulk_create): no MySQL o bulk_create não
            # devolve as PKs, e o histórico precisa delas. Tudo na mesma transação.
            with transaction.atomic():
                for palete in paletes:
                    palete.save()  # estado permanece FABRICADO
                registrar_historico(paletes, 'FABRICADO', usuario=request.user)
                transaction.on_commit(lambda: disparar_impressao_async(paletes))
            if quantidade == 1:
                messages.success(
                    request,
                    f'Palete {paletes[0].barcode_palete} (lote {lote}) impresso — '
                    'aguarde a bipagem de transferência pelo Gestor.',
                    extra_tags='tela_cheia',
                )
            else:
                messages.success(
                    request,
                    f'{quantidade} paletes do lote {lote} impressos — '
                    'aguarde a bipagem de transferência pelo Gestor.',
                    extra_tags='tela_cheia',
                )
            return redirect('producao:novo')
    else:
        form = RecebimentoForm()

    return render(request, 'producao/receber_palete.html', {
        'form': form,
        'peso_fixo_kg': PESO_FIXO_RECEBIMENTO_KG,
        'produtos_peso_variavel': list(
            Produto.objects.filter(ativo=True, peso_variavel=True).values_list('pk', flat=True)
        ),
        'estoque_sap': {e.sku: f'{e.disponivel:.3f}' for e in EstoqueItemSAP.objects.all()},
        'skus_produtos': dict(Produto.objects.filter(ativo=True).values_list('pk', 'sku')),
        'peso_ultimo_enviado': request.POST.get('peso_ultimo', '') if request.method == 'POST' else '',
        'paletes_recentes': Palete.objects.order_by('-criado_em', '-pk')[:PALETES_RECENTES_NA_TELA],
    })


def _bipar(request, barcode, estado_atual, estado_novo, motivo_recusa):
    """Busca o Palete pelo barcode_palete e transiciona `estado_atual` -> `estado_novo`.
    A linha é travada (select_for_update) para que bipagens concorrentes, ou uma
    reprovação simultânea, não sobrescrevam umas às outras nem dupliquem o histórico.
    Em qualquer falha grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    with transaction.atomic():
        palete = Palete.objects.select_for_update().filter(barcode_palete=barcode).first()
        if palete is None:
            messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
            return None
        if palete.estado != estado_atual:
            messages.error(
                request,
                f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — {motivo_recusa}',
            )
            return None
        palete.estado = estado_novo
        palete.save(update_fields=['estado', 'atualizado_em'])
        registrar_historico([palete], estado_novo, usuario=request.user)
    return palete


def _bipar_para_transferencia(request, barcode):
    """FABRICADO -> TRANSFERENCIA_PRODUCAO."""
    return _bipar(request, barcode, 'FABRICADO', 'TRANSFERENCIA_PRODUCAO', 'não pode ser bipado para transferência agora.')


def _bipar_para_confirmacao_logistica(request, barcode):
    """APROVADO_PRODUCAO -> TRANSFERENCIA_LOGISTICA."""
    return _bipar(
        request, barcode, 'APROVADO_PRODUCAO', 'TRANSFERENCIA_LOGISTICA',
        'não pode ser bipado para conferência da logística agora.',
    )


def _agrupar_por_lote(paletes):
    """Agrupa os paletes por lote, produto e data de fabricação em uma lista de dicts com
    agregados prontos para o template (peso total, quantidade)."""
    def chave_grupo(p):
        return p.lote, p.sku, p.data_fabricacao_exibida

    grupos = []
    for (lote, sku, data), itens in itertools.groupby(sorted(paletes, key=chave_grupo), key=chave_grupo):
        itens = list(itens)
        grupos.append({
            'lote': lote,
            'chave': f'{lote}{SEP_CHAVE}{sku}{SEP_CHAVE}{data.isoformat()}',
            'data_fabricacao': data,
            'sku': sku,
            'descricao': itens[0].descricao,
            'quantidade': len(itens),
            'peso_total': sum(p.peso_kg for p in itens),
            'paletes': itens,
        })
    return grupos


def _parse_data_fabricacao(valor):
    """Interpreta o filtro `data_fabricacao` da querystring (YYYY-MM-DD); None se ausente ou inválido."""
    try:
        return date.fromisoformat(valor or '')
    except ValueError:
        return None


def _grupos_da_pagina(request, estados, estado_bipado):
    """Grupos pendentes já paginados; devolve também a querystring base dos links de página."""
    pendentes = Palete.objects.filter(estado__in=estados).order_by('lote', 'sku', 'criado_em')
    grupos = _agrupar_por_lote(pendentes)
    _marcar_bipagem(grupos, estado_bipado)
    pagina = Paginator(grupos, GRUPOS_POR_PAGINA_TRANSFERENCIA).get_page(request.GET.get('page'))
    params = {k: request.GET[k] for k in ('data_relatorio',) if request.GET.get(k)}
    prefixo = '?' + ''.join(f'{urlencode({k: v})}&' for k, v in params.items()) + 'page='
    return pagina, prefixo


def _marcar_bipagem(grupos, estado_bipado):
    """Para cada grupo (já contendo `paletes` nos dois estados — pendente e bipado),
    separa a contagem de bipados/pendentes e o total do lote, sem nenhuma query extra."""
    for grupo in grupos:
        bipados = [p for p in grupo['paletes'] if p.estado == estado_bipado]
        grupo['quantidade'] = len(bipados)
        grupo['reprovados'] = sum(1 for p in grupo['paletes'] if p.estado == 'REPROVADO')
        grupo['pendentes_bipagem'] = len(grupo['paletes']) - len(bipados)
        grupo['total_lote'] = len(grupo['paletes'])


def _usuario_em_grupo(user, *nomes_grupo):
    return user.is_superuser or user.groups.filter(name__in=nomes_grupo).exists()


def _reprovar(request, etapa):
    """Reprova o palete; devolve-o em caso de sucesso (o aviso é a animação na linha dele)."""
    try:
        return reprovar_palete(
            request.POST.get('reprovar_palete'), etapa,
            usuario=request.user, motivo=request.POST.get('motivo'),
        )
    except PaleteStateError as exc:
        messages.error(request, str(exc))
        return None


def _redirect_flash(nome_url, flash):
    """Volta para a tela de transferência; `flash` ('tipo:chave') faz a linha animar."""
    url = reverse(nome_url)
    return redirect(f'{url}?{urlencode({"flash": flash}, safe=":")}' if flash else url)


def _pk_ou_none(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _corrigir(request):
    form = CorrecaoPaleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Dados inválidos: ' + '; '.join(
            e for erros in form.errors.values() for e in erros
        ))
        return
    try:
        palete = corrigir_palete(
            _pk_ou_none(request.POST.get('corrigir_palete')),
            form.cleaned_data['produto'], form.cleaned_data['peso_kg'],
            form.cleaned_data['data_fabricacao'], usuario=request.user,
            data_vencimento=form.cleaned_data['data_vencimento'],
        )
    except PaleteStateError as exc:
        messages.error(request, str(exc))
    else:
        aviso = f'Palete {palete.barcode_palete} corrigido — bipe-o novamente para confirmar.'
        if palete.reimprimir_etiqueta:
            transaction.on_commit(lambda: disparar_impressao_async([palete]))
            aviso += ' Nova etiqueta enviada para impressão — substitua a anterior.'
        messages.success(request, aviso)


def _parse_data_relatorio(valor):
    """Interpreta o parâmetro `data_relatorio` da querystring (formato YYYY-MM-DD,
    o mesmo de <input type="date">) — devolve a data de hoje se ausente ou inválido."""
    if valor:
        try:
            return date.fromisoformat(valor)
        except ValueError:
            pass
    return timezone.localdate()


def _lotes_aprovados_na_data(campo_aprovado, data):
    """Lista os produtos de cada lote (`{'chave', 'lote', 'sku'}`) com pelo menos um
    palete aprovado (campo `campo_aprovado` preenchido) na `data` informada — usada
    para popular os checkboxes de geração de relatório na tela de Transferência."""
    pares = (
        Palete.objects.filter(**{f'{campo_aprovado}__date': data})
        .order_by()
        .values_list('lote', 'sku')
        .distinct()
    )
    return [
        {'chave': f'{lote}{SEP_CHAVE}{sku}', 'lote': lote, 'sku': sku}
        for lote, sku in sorted(pares)
    ]


def _aprovar_post(request, aprovar):
    """Aprova o produto (`aprovar_sku`) do lote (`aprovar_lote`) enviado no POST."""
    lote = request.POST.get('aprovar_lote')
    sku = request.POST.get('aprovar_sku') or None
    data = _parse_data_fabricacao(request.POST.get('aprovar_data'))
    try:
        aprovar(lote, usuario=request.user, sku=sku, data_fabricacao=data)
    except (LoteVazioError, LoteIncompletoError) as exc:
        messages.error(request, str(exc))
        return None
    chave = SEP_CHAVE.join(filter(None, (lote, sku, data and data.isoformat())))
    return f'lote_ok:{chave}'


@group_required('Administradores', 'Operador Produção', 'Gestor Produção')
def transferencia_gestor(request):
    pode_bipar = _usuario_em_grupo(request.user, 'Administradores', 'Operador Produção', 'Gestor Produção')
    pode_aprovar = _usuario_em_grupo(request.user, 'Administradores', 'Gestor Produção')

    if request.method == 'POST':
        flash = None
        if 'barcode_palete' in request.POST and (pode_bipar or pode_aprovar):
            palete = _bipar_para_transferencia(request, request.POST.get('barcode_palete'))
            flash = f'palete_ok:{palete.barcode_palete}' if palete else None
        elif 'reprovar_palete' in request.POST and (pode_bipar or pode_aprovar):
            palete = _reprovar(request, 'PRODUCAO')
            flash = f'palete_erro:{palete.barcode_palete}' if palete else None
        elif 'corrigir_palete' in request.POST and pode_aprovar:
            _corrigir(request)
        elif 'aprovar_lote' in request.POST and pode_aprovar:
            flash = _aprovar_post(request, aprovar_lote)
        elif 'gerar_relatorio' in request.POST and pode_aprovar:
            lotes_selecionados = request.POST.getlist('lote_relatorio')
            if not lotes_selecionados:
                messages.error(request, 'Selecione ao menos um lote para gerar o relatório.')
            else:
                url_relatorio = reverse('producao:relatorio_aprovacao_producao') + \
                    '?lotes=' + ','.join(lotes_selecionados)
                return redirect(url_relatorio)
        return _redirect_flash('producao:transferencia_gestor', flash)

    grupos_pagina, prefixo_pagina = _grupos_da_pagina(
        request, ['FABRICADO', 'TRANSFERENCIA_PRODUCAO', 'REPROVADO'], 'TRANSFERENCIA_PRODUCAO',
    )
    data_relatorio = _parse_data_relatorio(request.GET.get('data_relatorio'))
    return render(request, 'producao/transferencia_gestor.html', {
        'grupos': grupos_pagina,
        'prefixo_pagina': prefixo_pagina,
        'produtos': Produto.objects.filter(ativo=True).order_by('sku') if pode_aprovar else [],
        'pode_reprovar': pode_bipar or pode_aprovar,
        'pode_bipar': pode_bipar,
        'pode_aprovar': pode_aprovar,
        'data_relatorio': data_relatorio,
        'lotes_data_relatorio': _lotes_aprovados_na_data('aprovado_producao_em', data_relatorio) if pode_aprovar else [],
    })


@group_required('Administradores', 'Operador Logística', 'Gestor Logística')
def transferencia_logistica(request):
    pode_bipar = _usuario_em_grupo(request.user, 'Administradores', 'Operador Logística', 'Gestor Logística')
    pode_aprovar = _usuario_em_grupo(request.user, 'Administradores', 'Gestor Logística')

    if request.method == 'POST':
        flash = None
        if 'barcode_palete' in request.POST and pode_bipar:
            palete = _bipar_para_confirmacao_logistica(request, request.POST.get('barcode_palete'))
            flash = f'palete_ok:{palete.barcode_palete}' if palete else None
        elif 'reprovar_palete' in request.POST and (pode_bipar or pode_aprovar):
            palete = _reprovar(request, 'LOGISTICA')
            flash = f'palete_erro:{palete.barcode_palete}' if palete else None
        elif 'aprovar_lote' in request.POST and pode_aprovar:
            flash = _aprovar_post(request, aprovar_lote_logistica)
        elif 'gerar_relatorio' in request.POST and pode_aprovar:
            lotes_selecionados = request.POST.getlist('lote_relatorio')
            if not lotes_selecionados:
                messages.error(request, 'Selecione ao menos um lote para gerar o relatório.')
            else:
                url_relatorio = reverse('producao:relatorio_aprovacao_logistica') + \
                    '?lotes=' + ','.join(lotes_selecionados)
                return redirect(url_relatorio)
        return _redirect_flash('producao:transferencia_logistica', flash)

    grupos_pagina, prefixo_pagina = _grupos_da_pagina(
        request, ['APROVADO_PRODUCAO', 'TRANSFERENCIA_LOGISTICA'], 'TRANSFERENCIA_LOGISTICA',
    )
    data_relatorio = _parse_data_relatorio(request.GET.get('data_relatorio'))
    return render(request, 'producao/transferencia_logistica.html', {
        'grupos': grupos_pagina,
        'prefixo_pagina': prefixo_pagina,
        'pode_reprovar': pode_bipar or pode_aprovar,
        'pode_bipar': pode_bipar,
        'pode_aprovar': pode_aprovar,
        'data_relatorio': data_relatorio,
        'lotes_data_relatorio': _lotes_aprovados_na_data('aprovado_logistica_em', data_relatorio) if pode_aprovar else [],
    })


def _filtro_chaves(chaves):
    """Q dos produtos de lote pedidos no relatório (`lote|sku`; só `lote` = todos os produtos)."""
    filtro = Q(pk__in=[])
    for chave in chaves:
        lote, _, sku = chave.partition(SEP_CHAVE)
        filtro |= Q(lote=lote, sku=sku) if sku else Q(lote=lote)
    return filtro


def _resumo_grupos_aprovados(lotes, campo_aprovado, estado_historico):
    """Monta os dados agregados por lote (SKU/descrição/peso total) e resolve
    o Gestor + data de aprovação a partir de PaleteHistorico, para os relatórios
    de aprovação — sem precisar de nenhum campo novo no banco."""
    paletes = Palete.objects.filter(_filtro_chaves(lotes), **{f'{campo_aprovado}__isnull': False}).order_by('lote', 'sku', 'criado_em')
    grupos = _agrupar_por_lote(paletes)
    aprovado_em = max((getattr(p, campo_aprovado) for p in paletes), default=None)
    historico = PaleteHistorico.objects.filter(
        palete__in=paletes, estado=estado_historico,
    ).select_related('usuario').order_by('-criado_em').first()
    gestor = historico.usuario if historico else None
    return grupos, gestor, aprovado_em


@group_required('Administradores', 'Gestor Produção')
def relatorio_aprovacao_producao(request):
    lotes = [l for l in request.GET.get('lotes', '').split(',') if l]
    grupos, gestor, aprovado_em = _resumo_grupos_aprovados(lotes, 'aprovado_producao_em', 'APROVADO_PRODUCAO')
    return render(request, 'producao/relatorio_aprovacao.html', {
        'titulo': 'Relatório de Aprovação — Produção',
        'grupos': grupos,
        'gestor': gestor or request.user,
        'aprovado_em': aprovado_em,
        'emitido_em': timezone.now(),
    })


@group_required('Administradores', 'Gestor Logística')
def relatorio_aprovacao_logistica(request):
    lotes = [l for l in request.GET.get('lotes', '').split(',') if l]
    grupos, gestor, aprovado_em = _resumo_grupos_aprovados(lotes, 'aprovado_logistica_em', 'APROVADO_LOGISTICA')
    return render(request, 'producao/relatorio_aprovacao.html', {
        'titulo': 'Relatório de Aprovação — Logística',
        'grupos': grupos,
        'gestor': gestor or request.user,
        'aprovado_em': aprovado_em,
        'emitido_em': timezone.now(),
    })


GRUPOS_ESTATISTICA_PRODUCAO = ('Administradores', 'Gestor Produção')
OPS_POR_PAGINA = 25


ORDENS_ESTOQUE = {
    'sku_asc': 'SKU: crescente',
    'sku_desc': 'SKU: decrescente',
    'saldo_desc': 'Saldo: maior primeiro',
    'saldo_asc': 'Saldo: menor primeiro',
}


def _estatisticas_estoque(request):
    """Aba "Estoque por produto": um saldo por SKU (SAP), sem repetir por OP."""
    busca = request.GET.get('q', '').strip()
    ordem = request.GET.get('ordem', '')
    if ordem not in ORDENS_ESTOQUE:
        ordem = 'sku_asc'
    descricoes = dict(Produto.objects.values_list('sku', 'descricao'))
    for sku, descricao in OrdemProducao.objects.exclude(descricao='').order_by('data_lancamento').values_list('sku', 'descricao'):
        descricoes.setdefault(sku, descricao)
    itens = EstoqueItemSAP.objects.all()
    if busca:
        itens = [i for i in itens if busca.lower() in i.sku.lower() or busca.lower() in descricoes.get(i.sku, '').lower()]
    else:
        itens = list(itens)
    maior = max((i.disponivel for i in itens), default=0) or Decimal('1')
    for item in itens:
        item.descricao = descricoes.get(item.sku, '')
        item.pct = int(max(item.disponivel, 0) * 100 / maior)
        item.nivel = 'alto' if item.pct >= 60 else 'medio' if item.pct >= 25 else 'baixo'
    itens.sort(key=(lambda i: i.disponivel) if ordem.startswith('saldo') else (lambda i: i.sku),
               reverse=ordem.endswith('desc'))
    filtros = {k: v for k, v in (('aba', 'estoque'), ('q', busca), ('ordem', ordem if ordem != 'sku_asc' else '')) if v}
    return render(request, 'producao/estatisticas.html', {
        'aba': 'estoque', 'pagina': Paginator(itens, OPS_POR_PAGINA).get_page(request.GET.get('page')),
        'busca': busca, 'ordem': ordem, 'opcoes_ordem': list(ORDENS_ESTOQUE.items()),
        'ultima_sync': max((i.sincronizado_em for i in itens), default=None),
        'total_skus': len(itens), 'total_saldo': sum((i.disponivel for i in itens), Decimal('0')),
        'total_saldo_num': int(sum((i.disponivel for i in itens), Decimal('0'))),
        'qtd_baixo': sum(1 for i in itens if i.nivel == 'baixo'),
        'prefixo_pagina': '?' + urlencode(filtros) + '&page=',
    })


STATUS_FILTRO_OPS = ('Liberada', 'Encerrada')
ORDENS_OPS = {
    'data_desc': ('Data: mais recente primeiro', ('-data_lancamento', '-doc_num')),
    'data_asc': ('Data: mais antiga primeiro', ('data_lancamento', 'doc_num')),
    'sku_asc': ('SKU: crescente', ('sku', '-data_lancamento')),
    'sku_desc': ('SKU: decrescente', ('-sku', '-data_lancamento')),
}


@group_required(*GRUPOS_ESTATISTICA_PRODUCAO)
def estatisticas_producao(request):
    if request.method == 'POST':
        try:
            n_ops, n_itens = sincronizar_sap()
        except SapIndisponivelError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'SAP sincronizado: {n_ops} ordens e {n_itens} itens.')
        return redirect('producao:estatisticas')

    if request.GET.get('aba') == 'estoque':
        return _estatisticas_estoque(request)

    desde = timezone.localdate() - timedelta(days=30)
    ops = OrdemProducao.objects.filter(data_lancamento__gte=desde)
    busca = request.GET.get('q', '').strip()
    if busca:
        ops = ops.filter(Q(sku__icontains=busca) | Q(descricao__icontains=busca))
    status = request.GET.get('status', '')
    if status not in STATUS_FILTRO_OPS:
        status = ''
    if status:
        ops = ops.filter(status=status)
    ordem = request.GET.get('ordem', '')
    if ordem not in ORDENS_OPS:
        ordem = 'data_desc'
    ops = ops.order_by(*ORDENS_OPS[ordem][1])
    estoque = {e.sku: e for e in EstoqueItemSAP.objects.all()}
    pagina = Paginator(ops, OPS_POR_PAGINA).get_page(request.GET.get('page'))
    for op in pagina:
        # Barras da animação "Planejado -> Produzido", na mesma escala.
        maior = max(op.quantidade_planejada, op.quantidade_produzida, Decimal('0.001'))
        op.pct_planejado = int(op.quantidade_planejada * 100 / maior)
        op.pct_produzido = int(op.quantidade_produzida * 100 / maior)
    total = ops.aggregate(plan=Sum('quantidade_planejada'), prod=Sum('quantidade_produzida'))
    ultima = max((e.sincronizado_em for e in estoque.values()), default=None)
    filtros = {k: v for k, v in (('q', busca), ('status', status), ('ordem', ordem if ordem != 'data_desc' else '')) if v}
    return render(request, 'producao/estatisticas.html', {
        'pagina': pagina, 'busca': busca, 'ultima_sync': ultima,
        'status': status, 'ordem': ordem, 'aba': 'ops',
        'opcoes_status': STATUS_FILTRO_OPS, 'opcoes_ordem': [(k, v[0]) for k, v in ORDENS_OPS.items()],
        'total_ops': ops.count(), 'total_skus': ops.values('sku').distinct().count(),
        'total_planejado': total['plan'] or 0, 'total_produzido': total['prod'] or 0,
        'prefixo_pagina': '?' + (urlencode(filtros) + '&' if filtros else '') + 'page=',
    })
