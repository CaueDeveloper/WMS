from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .models import Notificacao
from .notificacoes import LIMITE_PAINEL, marcar_lidas, nao_lidas

POR_PAGINA = 30


def _ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _destino_seguro(request, url):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return 'notificacoes'


def _serializar(n):
    return {
        'id': n.pk,
        'titulo': n.titulo,
        'mensagem': n.mensagem,
        'link': n.link,
        'lida': n.lida,
        'criada_em': timezone.localtime(n.criada_em).strftime('%d/%m/%Y %H:%M'),
    }


@login_required
@require_GET
def notificacoes_json(request):
    itens = request.user.notificacoes.all()[:LIMITE_PAINEL]
    return JsonResponse({
        'nao_lidas': nao_lidas(request.user),
        'itens': [_serializar(n) for n in itens],
    })


@login_required
@require_POST
def notificacao_ler(request, pk):
    """Marca a notificação como lida. Via fetch responde 204; via formulário
    redireciona para a tela de destino da notificação."""
    notificacao = get_object_or_404(Notificacao, pk=pk, usuario=request.user)
    marcar_lidas(request.user, ids=[pk])
    if _ajax(request):
        return HttpResponse(status=204)
    return redirect(_destino_seguro(request, notificacao.link))


@login_required
@require_POST
def notificacoes_ler_todas(request):
    marcar_lidas(request.user)
    if _ajax(request):
        return HttpResponse(status=204)
    return redirect('notificacoes')


@login_required
@require_GET
def notificacoes(request):
    pagina = Paginator(request.user.notificacoes.all(), POR_PAGINA).get_page(request.GET.get('pg'))
    return render(request, 'core/notificacoes.html', {'pagina': pagina, 'nao_lidas': nao_lidas(request.user)})
