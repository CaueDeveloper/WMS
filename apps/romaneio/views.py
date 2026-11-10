from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import group_required
from apps.logistica.services import PaleteNaoArmazenadoError, PesoInvalidoError
from apps.producao import sap
from apps.producao.sap import SapIndisponivelError

from .forms import RomaneioForm
from .models import Romaneio
from .services import (
    ColetaError,
    NotaJaRomaneadaError,
    QuantidadeIndisponivelError,
    RomaneioSemItensError,
    coletar_item,
    confirmar_coleta,
    criar_romaneio_de_notas,
    nota_com_disponibilidade,
    notas_com_disponibilidade,
    resumo_pendente,
)

GRUPOS_ROMANEIO = ('Administradores', 'Gestor Logística')
# O Operador Logística só consulta os romaneios já criados (lista, detalhe e impressão).
GRUPOS_CONSULTA = GRUPOS_ROMANEIO + ('Operador Logística',)
ITENS_POR_PAGINA = 20


def _pode_gerir(user):
    return user.is_superuser or user.groups.filter(name__in=GRUPOS_ROMANEIO).exists()


@group_required(*GRUPOS_CONSULTA)
def lista(request):
    romaneios = (
        Romaneio.objects.select_related('criado_por')
        .prefetch_related('itens', 'notas')
    )
    return render(request, 'romaneio/lista.html', {
        'romaneios': Paginator(romaneios, ITENS_POR_PAGINA).get_page(request.GET.get('pg')),
        'pode_gerir': _pode_gerir(request.user),
    })


CACHE_NOTAS = 'romaneio:notas_resumo'
CACHE_NOTAS_SEGUNDOS = 45


def _notas_sap(forcar=False):
    """Cabeçalhos das notas de saída com carga (sem linhas: com elas a consulta ao
    SAP leva ~18 s). Ficam em cache por poucos segundos; `forcar` ignora o cache."""
    notas = None if forcar else cache.get(CACHE_NOTAS)
    if notas is None:
        with sap.SapClient() as cliente:
            notas = cliente.notas_saida_resumo()
        cache.set(CACHE_NOTAS, notas, CACHE_NOTAS_SEGUNDOS)
    return notas


def _nota_sap(docentry):
    """A nota completa (com linhas), direto do SAP e sem cache."""
    with sap.SapClient() as cliente:
        return cliente.nota_saida(docentry)


@group_required(*GRUPOS_ROMANEIO)
def nota_itens(request, docentry):
    """Trecho HTML com as linhas e o plano de estoque de uma nota (carregado ao escolhê-la)."""
    try:
        nota = _nota_sap(docentry)
    except SapIndisponivelError as exc:
        return HttpResponse(f'Não foi possível consultar a nota no SAP. {exc}', status=502)
    nota = nota_com_disponibilidade(nota) if nota else None
    if nota is None:
        return HttpResponse('Nota indisponível (já tem romaneio ou perdeu o número da carga).', status=404)
    return render(request, 'romaneio/_nota_corpo.html', {'nota': nota})


@group_required(*GRUPOS_ROMANEIO)
def novo(request):
    """Novo romaneio a partir de uma ou mais Notas Fiscais de Saída do SAP (com
    Número da Carga), cada uma inteira; só dá para criar se o mapa cobrir todos
    os itens somados."""
    form = RomaneioForm(request.POST or None)
    notas, sap_erro, escolhidas_notas = [], False, {}
    escolhidas = list(dict.fromkeys(request.POST.getlist('nota'))) if request.method == 'POST' else []
    try:
        if request.method == 'POST' and form.is_valid():
            # Só as notas escolhidas são consultadas (sem cache): evita baixar todas de novo.
            sap_notas = [_nota_sap(int(e)) for e in escolhidas if e.isdigit()]
            if not escolhidas or len(sap_notas) != len(escolhidas) or None in sap_notas:
                messages.error(request, 'Escolha notas fiscais da lista (alguma pode ter perdido o número da carga no SAP).')
            else:
                try:
                    romaneio = criar_romaneio_de_notas(request.user, form.cleaned_data['data'], sap_notas)
                except (QuantidadeIndisponivelError, NotaJaRomaneadaError, PaleteNaoArmazenadoError,
                        PesoInvalidoError, RomaneioSemItensError) as exc:
                    messages.error(request, str(exc))
                    escolhidas_notas = {str(n['docentry']): n for n in notas_com_disponibilidade(sap_notas)}
                else:
                    cache.delete(CACHE_NOTAS)
                    messages.success(request, f'Romaneio {romaneio.pk} criado.', extra_tags='tela_cheia')
                    return redirect('romaneio:detalhe', pk=romaneio.pk)
        notas = _notas_sap(forcar=request.GET.get('atualizar') == '1')
    except SapIndisponivelError as exc:
        sap_erro = True
        messages.error(request, f'Não foi possível consultar as notas no SAP. {exc}')
    pendentes = resumo_pendente(notas)
    for n in pendentes:  # em caso de erro, a nota escolhida reaparece com o plano de estoque
        n['escolhida'] = str(n['docentry']) in escolhidas
        n['corpo'] = escolhidas_notas.get(str(n['docentry']))
    return render(request, 'romaneio/novo.html', {'form': form, 'notas': pendentes, 'sap_erro': sap_erro})


@group_required(*GRUPOS_CONSULTA)
def detalhe(request, pk):
    romaneio = get_object_or_404(
        Romaneio.objects.select_related('criado_por')
        .prefetch_related('notas', 'itens__retirada__palete__endereco'),
        pk=pk,
    )
    itens = list(romaneio.itens.all())
    pendentes = [i for i in itens if not i.coletado_em]
    return render(request, 'romaneio/detalhe.html', {
        'romaneio': romaneio,
        'itens': itens,
        'filas': _filas_de_coleta(pendentes),
        'coletados_lista': [i for i in itens if i.coletado_em],
        'coletados': len(itens) - len(pendentes),
        'faltam': len(pendentes),
        'pct': int((len(itens) - len(pendentes)) * 100 / len(itens)) if itens else 0,
    })


def _filas_de_coleta(pendentes):
    """Itens pendentes por corredor e andar. Em cada fila, o primeiro é o da frente
    (o último armazenado sai primeiro); os demais esperam atrás dele."""
    def desde(item):
        palete = item.retirada.palete
        return palete.armazenado_em or palete.criado_em

    grupos = {}
    for item in sorted(pendentes, key=lambda i: (desde(i), i.pk), reverse=True):
        grupos.setdefault((item.corredor, item.andar), []).append(item)
    filas = [{
        'corredor': corredor, 'andar': andar, 'proximo': itens[0], 'atras': itens[1:],
        'no_mapa': itens[0].retirada.palete.estado == 'ARMAZENADO',
    } for (corredor, andar), itens in grupos.items()]
    return sorted(filas, key=lambda f: (f['corredor'] == '', f['corredor'], f['andar'] or 0))


@group_required(*GRUPOS_CONSULTA)
@require_POST
def coletar(request, pk):
    """Bipagem de um palete do romaneio (ou clique em "Confirmar coleta")."""
    romaneio = get_object_or_404(Romaneio, pk=pk)
    try:
        if request.POST.get('acao') == 'confirmar':
            confirmar_coleta(romaneio, request.user)
            messages.success(request, f'Coleta do romaneio {romaneio.pk} confirmada.')
        else:
            item = coletar_item(romaneio, request.POST.get('barcode_palete'), request.user)
            messages.success(request, f'Palete {item.retirada.palete.barcode_palete} coletado.')
    except ColetaError as exc:
        messages.error(request, str(exc))
    return redirect('romaneio:detalhe', pk=romaneio.pk)


@group_required(*GRUPOS_CONSULTA)
def imprimir(request, pk):
    """Romaneio em A4, sem o shell do sistema, pronto para imprimir e assinar."""
    romaneio = get_object_or_404(
        Romaneio.objects.select_related('criado_por')
        .prefetch_related('notas', 'itens__retirada__palete'),
        pk=pk,
    )
    return render(request, 'romaneio/imprimir.html', {
        'romaneio': romaneio,
        'emitido_em': timezone.localtime(),
        'auto': request.GET.get('auto') == '1',
    })
