from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render

from apps.recebimento.models import Palete

from .models import Endereco
from .services import EnderecoCheioError, alocar_palete

ANDARES = ['A', 'B', 'C']


def _nivel_ocupacao(ocupacao_atual, capacidade):
    if capacidade <= 0 or ocupacao_atual <= 0:
        return 'vazio'
    percentual = ocupacao_atual / capacidade * 100
    if percentual >= 100:
        return 'cheio'
    if percentual >= 70:
        return 'alto'
    if percentual >= 30:
        return 'medio'
    return 'baixo'


@login_required
def putaway(request):
    if request.method == 'POST':
        palete = get_object_or_404(Palete, pk=request.POST.get('palete_id'), estado='CONFERIDO')
        codigo = request.POST.get('codigo_endereco')
        try:
            endereco = alocar_palete(palete, codigo)
        except Endereco.DoesNotExist:
            messages.error(request, f'Endereço "{codigo}" não encontrado, inativo ou não existe mais.')
        except EnderecoCheioError as exc:
            messages.error(request, f'{exc} Escolha outra posição.')
        else:
            messages.success(request, f'Palete {palete.barcode_palete} armazenado em {endereco.codigo}.')
        return redirect('estoque:putaway')

    pendentes = Palete.objects.filter(estado='CONFERIDO').order_by('criado_em')
    enderecos_disponiveis = Endereco.objects.filter(
        ativo=True, ocupacao_atual__lt=F('capacidade'),
    ).order_by('corredor', 'andar')

    return render(request, 'estoque/putaway.html', {
        'pendentes': pendentes,
        'enderecos': enderecos_disponiveis,
    })


@login_required
def mapa_estoque(request):
    enderecos_por_codigo = {e.codigo: e for e in Endereco.objects.all()}

    grade = []
    for corredor in range(1, 21):
        celulas = []
        for andar in ANDARES:
            endereco = enderecos_por_codigo.get(f'{corredor:02d}-{andar}')
            if endereco:
                celulas.append({
                    'endereco': endereco,
                    'nivel': _nivel_ocupacao(endereco.ocupacao_atual, endereco.capacidade),
                })
            else:
                celulas.append(None)
        grade.append({'corredor': corredor, 'celulas': celulas})

    paletes_armazenados = list(
        Palete.objects.filter(estado='ARMAZENADO')
        .select_related('endereco', 'area_destino')
        .order_by('endereco__corredor', 'endereco__andar', 'atualizado_em')
    )

    totais = Endereco.objects.filter(ativo=True).aggregate(
        capacidade=Sum('capacidade'), ocupacao=Sum('ocupacao_atual'),
    )
    capacidade_total = totais['capacidade'] or 0
    ocupacao_total = totais['ocupacao'] or 0
    percentual_geral = round(ocupacao_total / capacidade_total * 100) if capacidade_total else 0

    return render(request, 'estoque/mapa_estoque.html', {
        'grade': grade,
        'andares': ANDARES,
        'paletes_armazenados': paletes_armazenados,
        'total_paletes_armazenados': len(paletes_armazenados),
        'capacidade_total': capacidade_total,
        'ocupacao_total': ocupacao_total,
        'percentual_geral': percentual_geral,
    })
