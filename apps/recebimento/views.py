from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.decorators import group_required
from apps.estoque.models import AreaDestino

from .forms import RecebimentoForm
from .models import Palete, gerar_proximo_lote
from .zpl import disparar_impressao_async


@login_required
def receber_palete(request):
    if request.method == 'POST':
        form = RecebimentoForm(request.POST)
        if form.is_valid():
            produto = form.cleaned_data['produto']
            peso_kg = form.cleaned_data['peso_kg']

            palete = Palete(
                sku=produto.sku,
                descricao=produto.descricao,
                lote=gerar_proximo_lote(),
                peso_kg=peso_kg,
            )
            # STATUS_CHOICES rotula DIVERGENTE como "Divergência de Peso":
            # peso <= 0 é a única condição de divergência que o modelo atual permite verificar.
            if peso_kg <= 0:
                palete.estado = 'DIVERGENTE'
                palete.save()
                messages.error(
                    request,
                    f'Palete {palete.barcode_palete} marcado como DIVERGENTE (peso inválido). '
                    'Fluxo bloqueado — corrija a pesagem antes de prosseguir.',
                )
            else:
                palete.save()  # estado permanece CRIADO: aguardando conferência do Gestor
                messages.success(
                    request,
                    f'Palete {palete.barcode_palete} (lote {palete.lote}) enviado para conferência do Gestor.',
                )
            return redirect('recebimento:novo')
    else:
        form = RecebimentoForm()

    return render(request, 'recebimento/receber_palete.html', {'form': form})


@group_required('Gestores')
def conferencia_gestor(request):
    if request.method == 'POST':
        palete = get_object_or_404(Palete, pk=request.POST.get('palete_id'), estado='CRIADO')
        acao = request.POST.get('acao')

        if acao == 'aprovar':
            area = get_object_or_404(AreaDestino, pk=request.POST.get('area_destino'), ativo=True)
            palete.estado = 'CONFERIDO'
            palete.area_destino = area
            palete.save(update_fields=['estado', 'area_destino', 'atualizado_em'])
            disparar_impressao_async(palete)
            messages.success(
                request,
                f'Palete {palete.barcode_palete} aprovado e enviado para impressão — destino: {area.nome}.',
            )
        elif acao == 'rejeitar':
            palete.estado = 'DIVERGENTE'
            palete.save(update_fields=['estado', 'atualizado_em'])
            messages.error(request, f'Palete {palete.barcode_palete} marcado como DIVERGENTE pelo Gestor.')

        return redirect('recebimento:conferencia_gestor')

    pendentes = Palete.objects.filter(estado='CRIADO').order_by('criado_em')
    areas = AreaDestino.objects.filter(ativo=True)
    return render(request, 'recebimento/conferencia_gestor.html', {'pendentes': pendentes, 'areas': areas})
