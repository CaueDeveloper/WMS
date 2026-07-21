from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.recebimento.models import Palete

from .decorators import group_required
from .forms import ReautenticacaoForm


@login_required
def home(request):
    return render(request, 'core/home.html')


@group_required('Gestores')
def relatorio_divergencias(request):
    paletes_armazenados = Palete.objects.filter(estado='ARMAZENADO').select_related('endereco').order_by('endereco__codigo')

    if request.method == 'POST':
        form = ReautenticacaoForm(request.POST)
        if form.is_valid() and request.user.check_password(form.cleaned_data['senha']):
            atualizados = paletes_armazenados.update(estado='FINALIZADO')
            messages.success(
                request,
                f'{atualizados} palete(s) confirmado(s) e sincronizado(s) com o SAP B1.',
            )
            return redirect('relatorio_divergencias')
        messages.error(request, 'Senha inválida. Autorização não concedida.')
    else:
        form = ReautenticacaoForm()

    return render(request, 'core/relatorio_divergencias.html', {
        'form': form,
        'paletes': paletes_armazenados,
    })
