import itertools
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.core.decorators import group_required

from .forms import RecebimentoForm
from .models import Palete
from .services import aprovar_lotes_pendentes
from .whatsapp import disparar_notificacao_async
from .zpl import disparar_impressao_async

PESO_FIXO_RECEBIMENTO_KG = Decimal('500')
GRUPOS_POR_PAGINA_TRANSFERENCIA = 5


@login_required
def receber_palete(request):
    if request.method == 'POST':
        form = RecebimentoForm(request.POST)
        if form.is_valid():
            produto = form.cleaned_data['produto']
            lote = form.cleaned_data['lote']

            palete = Palete(
                sku=produto.sku,
                descricao=produto.descricao,
                lote=lote,
                peso_kg=PESO_FIXO_RECEBIMENTO_KG,
            )
            palete.save()  # estado permanece CRIADO
            disparar_impressao_async(palete)
            messages.success(
                request,
                f'Palete {palete.barcode_palete} (lote {palete.lote}) impresso — '
                'aguarde a bipagem de transferência pelo Gestor.',
            )
            return redirect('recebimento:novo')
    else:
        form = RecebimentoForm()

    return render(request, 'recebimento/receber_palete.html', {'form': form})


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
    messages.success(request, f'Palete {palete.barcode_palete} (lote {palete.lote}) bipado para transferência.')
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


@group_required('Gestores')
def transferencia_gestor(request):
    if request.method == 'POST':
        if 'barcode_palete' in request.POST:
            _bipar_para_transferencia(request, request.POST.get('barcode_palete'))
        elif 'aprovar_pendentes' in request.POST:
            aprovados, falhas = aprovar_lotes_pendentes()
            if aprovados:
                disparar_notificacao_async(aprovados)
                total_paletes = sum(len(paletes) for _, paletes in aprovados)
                lotes_str = ', '.join(lote for lote, _ in aprovados)
                messages.success(
                    request,
                    f'{len(aprovados)} lote(s) aprovado(s) ({total_paletes} palete(s)): {lotes_str}. '
                    'Notificação enviada à logística.',
                )
            for _, erro in falhas:
                messages.error(request, erro)
            if not aprovados and not falhas:
                messages.error(request, 'Nenhum lote pendente de aprovação no momento.')
        return redirect('recebimento:transferencia_gestor')

    pendentes = Palete.objects.filter(estado='EM_TRANSFERENCIA').order_by('lote', 'criado_em')
    grupos = _agrupar_por_lote(pendentes)
    total_grupos = len(grupos)
    grupos_pagina = Paginator(grupos, GRUPOS_POR_PAGINA_TRANSFERENCIA).get_page(request.GET.get('page'))
    return render(request, 'recebimento/transferencia_gestor.html', {
        'grupos': grupos_pagina,
        'total_grupos': total_grupos,
    })
