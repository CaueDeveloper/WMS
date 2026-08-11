from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from urllib.parse import urlencode

from apps.recebimento.models import Palete

from .models import Endereco
from .services import (
    EnderecoCheioError,
    MesmoEnderecoError,
    PaleteNaoArmazenadoError,
    alocar_palete,
    mover_palete,
    retirar_palete_do_mapa,
)

GUARDAS = range(1, 21)
ANDARES = ['A', 'B', 'C']
LADOS = [1, 2]
ITENS_POR_PAGINA_MAPA = 20


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


def _enderecos_disponiveis():
    return Endereco.objects.filter(
        ativo=True, ocupacao_atual__lt=F('capacidade'),
    ).order_by('lado', 'guarda', 'andar')


def _construir_grade():
    """Monta a grade completa de endereços (todos, inclusive cheios/inativos),
    uma tabela por lado, linhas por guarda e colunas por andar — mesma estrutura
    consumida pelo mapa de estoque (somente leitura) e pelo put-away (seleção
    clicável, usa o `disponivel` de cada célula para decidir se vira botão)."""
    enderecos_por_codigo = {e.codigo: e for e in Endereco.objects.all()}
    grade_por_lado = []
    for lado in LADOS:
        linhas = []
        for guarda in GUARDAS:
            celulas = []
            for andar in ANDARES:
                codigo = Endereco.montar_codigo(lado=lado, andar=andar, guarda=guarda)
                endereco = enderecos_por_codigo.get(codigo)
                if endereco:
                    celulas.append({
                        'endereco': endereco,
                        'nivel': _nivel_ocupacao(endereco.ocupacao_atual, endereco.capacidade),
                        'disponivel': endereco.ativo and endereco.ocupacao_atual < endereco.capacidade,
                    })
                else:
                    celulas.append(None)
            linhas.append({'guarda': guarda, 'celulas': celulas})
        grade_por_lado.append({'lado': lado, 'linhas': linhas})
    return grade_por_lado


def _buscar_palete_conferido(request, barcode):
    """Busca Palete pelo barcode_palete e garante estado CONFERIDO.
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
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — não pode ser guardado agora.',
        )
        return None
    return palete


@login_required
def putaway(request):
    if request.method == 'POST':
        if 'codigo_endereco' in request.POST:
            # Etapa B: aloca o palete já localizado, no endereço bipado/digitado.
            palete = _buscar_palete_conferido(request, request.POST.get('barcode_palete'))
            if palete is not None:
                codigo = (request.POST.get('codigo_endereco') or '').strip()
                try:
                    endereco = alocar_palete(palete, codigo)
                except Endereco.DoesNotExist:
                    messages.error(request, f'Endereço "{codigo}" não encontrado, inativo ou não existe mais.')
                except EnderecoCheioError as exc:
                    messages.error(request, f'{exc} Escolha outra posição.')
                else:
                    messages.success(request, f'Palete {palete.barcode_palete} armazenado em {endereco.codigo}.')
            return redirect('estoque:putaway')

        # Etapa A: localiza o palete pelo barcode bipado.
        barcode = (request.POST.get('barcode_palete') or '').strip()
        palete = _buscar_palete_conferido(request, barcode)
        if palete is None:
            return redirect('estoque:putaway')
        return redirect(f"{reverse('estoque:putaway')}?{urlencode({'barcode': barcode})}")

    # GET: ?barcode=<code> mostra a Etapa B (produto + input de endereço);
    # sem parâmetro mostra a Etapa A (tela de bipagem).
    palete = None
    barcode = request.GET.get('barcode', '').strip()
    if barcode:
        palete = _buscar_palete_conferido(request, barcode)
        if palete is None:
            return redirect('estoque:putaway')

    return render(request, 'estoque/putaway.html', {
        'palete': palete,
        'grade_por_lado': _construir_grade(),
        'andares': ANDARES,
    })


@login_required
def mapa_estoque(request):
    grade_por_lado = _construir_grade()

    paginator_armazenados = Paginator(
        Palete.objects.filter(estado='ARMAZENADO')
        .select_related('endereco', 'area_destino')
        .order_by('endereco__lado', 'endereco__guarda', 'endereco__andar', 'atualizado_em'),
        ITENS_POR_PAGINA_MAPA,
    )
    paletes_armazenados = paginator_armazenados.get_page(request.GET.get('pg_armazenados'))

    paginator_pendentes = Paginator(
        Palete.objects.filter(estado='CONFERIDO')
        .select_related('area_destino')
        .order_by('lote', 'atualizado_em'),
        ITENS_POR_PAGINA_MAPA,
    )
    paletes_pendentes_armazenagem = paginator_pendentes.get_page(request.GET.get('pg_pendentes'))

    totais = Endereco.objects.filter(ativo=True).aggregate(
        capacidade=Sum('capacidade'), ocupacao=Sum('ocupacao_atual'),
    )
    capacidade_total = totais['capacidade'] or 0
    ocupacao_total = totais['ocupacao'] or 0
    percentual_geral = round(ocupacao_total / capacidade_total * 100) if capacidade_total else 0

    return render(request, 'estoque/mapa_estoque.html', {
        'grade_por_lado': grade_por_lado,
        'andares': ANDARES,
        'paletes_armazenados': paletes_armazenados,
        'total_paletes_armazenados': paginator_armazenados.count,
        'paletes_pendentes_armazenagem': paletes_pendentes_armazenagem,
        'total_paletes_pendentes_armazenagem': paginator_pendentes.count,
        'capacidade_total': capacidade_total,
        'ocupacao_total': ocupacao_total,
        'percentual_geral': percentual_geral,
    })


def _buscar_palete(request, barcode):
    """Busca Palete pelo barcode_palete, sem restrição de estado.
    Em caso de não encontrado grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.select_related('endereco', 'area_destino').filter(barcode_palete=barcode).first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    return palete


def _exigir_armazenado(request, palete):
    if palete.estado != 'ARMAZENADO':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" '
            '— não é possível mover ou retirar agora.',
        )
        return False
    return True


def _redirect_com_barcode(barcode):
    return redirect(f"{reverse('estoque:consultar_palete')}?{urlencode({'barcode': barcode})}")


@login_required
def consultar_palete(request):
    if request.method == 'POST':
        barcode = (request.POST.get('barcode_palete') or '').strip()

        if 'novo_codigo_endereco' in request.POST:
            palete = _buscar_palete(request, barcode)
            if palete is not None and _exigir_armazenado(request, palete):
                codigo = (request.POST.get('novo_codigo_endereco') or '').strip()
                try:
                    endereco = mover_palete(palete, codigo)
                except Endereco.DoesNotExist:
                    messages.error(request, f'Endereço "{codigo}" não encontrado, inativo ou não existe mais.')
                except EnderecoCheioError as exc:
                    messages.error(request, f'{exc} Escolha outra posição.')
                except (MesmoEnderecoError, PaleteNaoArmazenadoError) as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f'Palete {palete.barcode_palete} movido para {endereco.codigo}.')
            return _redirect_com_barcode(barcode) if barcode else redirect('estoque:consultar_palete')

        if 'retirar' in request.POST:
            palete = _buscar_palete(request, barcode)
            if palete is not None and _exigir_armazenado(request, palete):
                try:
                    retirar_palete_do_mapa(palete)
                except PaleteNaoArmazenadoError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f'Palete {palete.barcode_palete} retirado do mapa — saída física registrada.')
            return _redirect_com_barcode(barcode) if barcode else redirect('estoque:consultar_palete')

        # Bipagem inicial: localiza o palete (qualquer estado) e redireciona.
        palete = _buscar_palete(request, barcode)
        if palete is None:
            return redirect('estoque:consultar_palete')
        return _redirect_com_barcode(barcode)

    palete = None
    barcode = request.GET.get('barcode', '').strip()
    if barcode:
        palete = _buscar_palete(request, barcode)
        if palete is None:
            return redirect('estoque:consultar_palete')

    return render(request, 'estoque/consultar_palete.html', {
        'palete': palete,
        'enderecos': _enderecos_disponiveis() if palete and palete.estado == 'ARMAZENADO' else None,
    })
