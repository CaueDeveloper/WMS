import itertools
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.decorators import group_required
from apps.estoque.models import Produto

from .forms import CorrecaoPaleteForm, RecebimentoForm
from .models import Palete, PaleteHistorico, registrar_historico
from .services import (
    LoteIncompletoError, LoteVazioError, PaleteStateError, aprovar_lote, aprovar_lote_logistica,
    corrigir_palete, reprovar_palete,
)
from .zpl import disparar_impressao_async

PESO_FIXO_RECEBIMENTO_KG = Decimal('500')
GRUPOS_POR_PAGINA_TRANSFERENCIA = 5
PALETES_RECENTES_NA_TELA = 20


@group_required('Administradores', 'Operador Produção')
def receber_palete(request):
    if request.method == 'POST':
        form = RecebimentoForm(request.POST)
        if form.is_valid():
            produto = form.cleaned_data['produto']
            lote = form.cleaned_data['lote']
            quantidade = form.cleaned_data['quantidade']
            data_fabricacao = form.cleaned_data['data_fabricacao']
            peso_kg = form.cleaned_data['peso_kg'] if form.cleaned_data['peso_variavel'] else PESO_FIXO_RECEBIMENTO_KG

            paletes = [
                Palete(
                    sku=produto.sku, descricao=produto.descricao, lote=lote,
                    peso_kg=peso_kg, data_fabricacao=data_fabricacao,
                )
                for _ in range(quantidade)
            ]
            Palete.objects.bulk_create(paletes)  # estado permanece CRIADO
            registrar_historico(paletes, 'CRIADO', usuario=request.user)
            disparar_impressao_async(paletes)
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
            return redirect('recebimento:novo')
    else:
        form = RecebimentoForm()

    return render(request, 'recebimento/receber_palete.html', {
        'form': form,
        'peso_fixo_kg': PESO_FIXO_RECEBIMENTO_KG,
        'produtos_peso_variavel': list(
            Produto.objects.filter(ativo=True, peso_variavel=True).values_list('pk', flat=True)
        ),
        'paletes_recentes': Palete.objects.order_by('-criado_em', '-pk')[:PALETES_RECENTES_NA_TELA],
    })


def _bipar_para_transferencia(request, barcode):
    """Busca Palete pelo barcode_palete e transiciona CRIADO -> EM_TRANSFERENCIA.
    Em qualquer falha grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.filter(barcode_palete=barcode).first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    if palete.estado != 'CRIADO':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — '
            'não pode ser bipado para transferência agora.',
        )
        return None
    palete.estado = 'EM_TRANSFERENCIA'
    palete.save(update_fields=['estado', 'atualizado_em'])
    registrar_historico([palete], 'EM_TRANSFERENCIA', usuario=request.user)
    messages.success(request, f'Palete {palete.barcode_palete} (lote {palete.lote}) bipado para transferência.', extra_tags='tela_cheia tela_rapida')
    return palete


def _bipar_para_confirmacao_logistica(request, barcode):
    """Busca Palete pelo barcode_palete e transiciona CONFERIDO -> EM_CONFERENCIA_LOGISTICA.
    Em qualquer falha grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.filter(barcode_palete=barcode).first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    if palete.estado != 'CONFERIDO':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — '
            'não pode ser bipado para conferência da logística agora.',
        )
        return None
    palete.estado = 'EM_CONFERENCIA_LOGISTICA'
    palete.save(update_fields=['estado', 'atualizado_em'])
    registrar_historico([palete], 'EM_CONFERENCIA_LOGISTICA', usuario=request.user)
    messages.success(request, f'Palete {palete.barcode_palete} (lote {palete.lote}) bipado para conferência da logística.', extra_tags='tela_cheia tela_rapida')
    return palete


def _agrupar_por_lote(paletes_ordenados_por_lote):
    """Agrupa um queryset já ordenado por `lote` em uma lista de dicts com agregados
    prontos para o template (peso total, quantidade)."""
    grupos = []
    for lote, itens in itertools.groupby(paletes_ordenados_por_lote, key=lambda p: p.lote):
        itens = list(itens)
        grupos.append({
            'lote': lote,
            'sku': itens[0].sku,
            'descricao': itens[0].descricao,
            'quantidade': len(itens),
            'peso_total': sum(p.peso_kg for p in itens),
            'paletes': itens,
        })
    return grupos


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
    try:
        palete = reprovar_palete(
            request.POST.get('reprovar_palete'), etapa,
            usuario=request.user, motivo=request.POST.get('motivo'),
        )
    except PaleteStateError as exc:
        messages.error(request, str(exc))
    else:
        messages.warning(
            request,
            f'Palete {palete.barcode_palete} (lote {palete.lote}) reprovado — enviado ao Gestor para correção.',
            extra_tags='tela_cheia tela_rapida tela_erro',
        )


def _corrigir(request):
    form = CorrecaoPaleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Dados inválidos: ' + '; '.join(
            e for erros in form.errors.values() for e in erros
        ))
        return
    try:
        palete = corrigir_palete(
            request.POST.get('corrigir_palete'),
            form.cleaned_data['produto'], form.cleaned_data['peso_kg'],
            form.cleaned_data['data_fabricacao'], usuario=request.user,
        )
    except PaleteStateError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f'Palete {palete.barcode_palete} corrigido — bipe-o novamente para confirmar.',
        )


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
    """Lista os lotes com pelo menos um palete aprovado (campo `campo_aprovado`
    preenchido) na `data` informada — usada para popular os checkboxes de
    geração de relatório na tela de Transferência."""
    return list(
        Palete.objects.filter(**{f'{campo_aprovado}__date': data})
        .order_by('-' + campo_aprovado)
        .values_list('lote', flat=True)
        .distinct()
    )


@group_required('Administradores', 'Operador Produção', 'Gestor Produção')
def transferencia_gestor(request):
    pode_bipar = _usuario_em_grupo(request.user, 'Administradores', 'Operador Produção')
    pode_aprovar = _usuario_em_grupo(request.user, 'Administradores', 'Gestor Produção')

    if request.method == 'POST':
        if 'barcode_palete' in request.POST and (pode_bipar or pode_aprovar):
            _bipar_para_transferencia(request, request.POST.get('barcode_palete'))
        elif 'reprovar_palete' in request.POST and (pode_bipar or pode_aprovar):
            _reprovar(request, 'PRODUCAO')
        elif 'corrigir_palete' in request.POST and pode_aprovar:
            _corrigir(request)
        elif 'aprovar_lote' in request.POST and pode_aprovar:
            lote = request.POST.get('aprovar_lote')
            try:
                paletes = aprovar_lote(lote, usuario=request.user)
            except (LoteVazioError, LoteIncompletoError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Lote {lote} aprovado ({len(paletes)} palete(s)).',
                    extra_tags='tela_cheia',
                )
        elif 'gerar_relatorio' in request.POST and pode_aprovar:
            lotes_selecionados = request.POST.getlist('lote_relatorio')
            if not lotes_selecionados:
                messages.error(request, 'Selecione ao menos um lote para gerar o relatório.')
            else:
                url_relatorio = reverse('recebimento:relatorio_aprovacao_producao') + \
                    '?lotes=' + ','.join(lotes_selecionados)
                return redirect(url_relatorio)
        return redirect('recebimento:transferencia_gestor')

    pendentes = Palete.objects.filter(
        estado__in=['CRIADO', 'EM_TRANSFERENCIA', 'REPROVADO'],
    ).order_by('lote', 'criado_em')
    grupos = _agrupar_por_lote(pendentes)
    _marcar_bipagem(grupos, estado_bipado='EM_TRANSFERENCIA')
    grupos_pagina = Paginator(grupos, GRUPOS_POR_PAGINA_TRANSFERENCIA).get_page(request.GET.get('page'))
    data_relatorio = _parse_data_relatorio(request.GET.get('data_relatorio'))
    return render(request, 'recebimento/transferencia_gestor.html', {
        'grupos': grupos_pagina,
        'produtos': Produto.objects.filter(ativo=True).order_by('sku') if pode_aprovar else [],
        'pode_reprovar': pode_bipar or pode_aprovar,
        'pode_bipar': pode_bipar,
        'pode_aprovar': pode_aprovar,
        'data_relatorio': data_relatorio,
        'lotes_data_relatorio': _lotes_aprovados_na_data('aprovado_producao_em', data_relatorio) if pode_aprovar else [],
    })


@group_required('Administradores', 'Operador Logística', 'Gestor Logística')
def transferencia_logistica(request):
    pode_bipar = _usuario_em_grupo(request.user, 'Administradores', 'Operador Logística')
    pode_aprovar = _usuario_em_grupo(request.user, 'Administradores', 'Gestor Logística')

    if request.method == 'POST':
        if 'barcode_palete' in request.POST and pode_bipar:
            _bipar_para_confirmacao_logistica(request, request.POST.get('barcode_palete'))
        elif 'reprovar_palete' in request.POST and pode_bipar:
            _reprovar(request, 'LOGISTICA')
        elif 'aprovar_lote' in request.POST and pode_aprovar:
            lote = request.POST.get('aprovar_lote')
            try:
                paletes = aprovar_lote_logistica(lote, usuario=request.user)
            except (LoteVazioError, LoteIncompletoError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Lote {lote} confirmado pela logística ({len(paletes)} palete(s)). '
                    'Liberado(s) para o Mapa de Estoque e put-away.',
                    extra_tags='tela_cheia',
                )
        elif 'gerar_relatorio' in request.POST and pode_aprovar:
            lotes_selecionados = request.POST.getlist('lote_relatorio')
            if not lotes_selecionados:
                messages.error(request, 'Selecione ao menos um lote para gerar o relatório.')
            else:
                url_relatorio = reverse('recebimento:relatorio_aprovacao_logistica') + \
                    '?lotes=' + ','.join(lotes_selecionados)
                return redirect(url_relatorio)
        return redirect('recebimento:transferencia_logistica')

    pendentes = Palete.objects.filter(
        estado__in=['CONFERIDO', 'EM_CONFERENCIA_LOGISTICA'],
    ).order_by('lote', 'criado_em')
    grupos = _agrupar_por_lote(pendentes)
    _marcar_bipagem(grupos, estado_bipado='EM_CONFERENCIA_LOGISTICA')
    grupos_pagina = Paginator(grupos, GRUPOS_POR_PAGINA_TRANSFERENCIA).get_page(request.GET.get('page'))
    data_relatorio = _parse_data_relatorio(request.GET.get('data_relatorio'))
    return render(request, 'recebimento/transferencia_logistica.html', {
        'grupos': grupos_pagina,
        'pode_reprovar': pode_bipar,
        'pode_bipar': pode_bipar,
        'pode_aprovar': pode_aprovar,
        'data_relatorio': data_relatorio,
        'lotes_data_relatorio': _lotes_aprovados_na_data('aprovado_logistica_em', data_relatorio) if pode_aprovar else [],
    })


def _resumo_grupos_aprovados(lotes, campo_aprovado, estado_historico):
    """Monta os dados agregados por lote (SKU/descrição/peso total) e resolve
    o Gestor + data de aprovação a partir de PaleteHistorico, para os relatórios
    de aprovação — sem precisar de nenhum campo novo no banco."""
    paletes = Palete.objects.filter(lote__in=lotes, **{f'{campo_aprovado}__isnull': False}).order_by('lote', 'criado_em')
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
    grupos, gestor, aprovado_em = _resumo_grupos_aprovados(lotes, 'aprovado_producao_em', 'CONFERIDO')
    return render(request, 'recebimento/relatorio_aprovacao.html', {
        'titulo': 'Relatório de Aprovação — Produção',
        'grupos': grupos,
        'gestor': gestor or request.user,
        'aprovado_em': aprovado_em,
        'emitido_em': timezone.now(),
    })


@group_required('Administradores', 'Gestor Logística')
def relatorio_aprovacao_logistica(request):
    lotes = [l for l in request.GET.get('lotes', '').split(',') if l]
    grupos, gestor, aprovado_em = _resumo_grupos_aprovados(lotes, 'aprovado_logistica_em', 'CONFERIDO_LOGISTICA')
    return render(request, 'recebimento/relatorio_aprovacao.html', {
        'titulo': 'Relatório de Aprovação — Logística',
        'grupos': grupos,
        'gestor': gestor or request.user,
        'aprovado_em': aprovado_em,
        'emitido_em': timezone.now(),
    })
