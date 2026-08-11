from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.recebimento.models import Palete

from .decorators import group_required
from .forms import ReautenticacaoForm

ITENS_POR_PAGINA_CONFIRMACAO = 20


@login_required
def home(request):
    return render(request, 'core/home.html')


@group_required('Gestores')
def confirmar_armazenagem(request):
    # Sempre a queryset completa (não paginada) — o .update() abaixo precisa afetar
    # todos os paletes ARMAZENADO, nunca só a página exibida no GET.
    paletes_armazenados = Palete.objects.filter(estado='ARMAZENADO').select_related('endereco').order_by('endereco__codigo')

    if request.method == 'POST':
        form = ReautenticacaoForm(request.POST)
        if form.is_valid() and request.user.check_password(form.cleaned_data['senha']):
            atualizados = paletes_armazenados.update(estado='FINALIZADO')
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
