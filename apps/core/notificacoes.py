"""Notificações in-app. Para criar um novo evento: acrescente o tipo em
Notificacao.TIPO_CHOICES e chame `notificar_grupos(...)` no serviço que conclui
a etapa — dentro da transação, já que o envio é adiado para o commit."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import Notificacao

LIMITE_PAINEL = 20


def notificar_grupos(grupos, tipo, titulo, mensagem, link='', excluir=None):
    """Cria uma notificação para cada usuário ativo de qualquer um dos `grupos`.
    Adiada para depois do commit: se a transação que disparou o evento for
    desfeita, ninguém é notificado. `excluir` (usuário) não recebe a própria
    notificação."""
    excluir_pk = getattr(excluir, 'pk', None)

    def _criar():
        destinatarios = get_user_model().objects.filter(is_active=True, groups__name__in=grupos).distinct()
        if excluir_pk:
            destinatarios = destinatarios.exclude(pk=excluir_pk)
        Notificacao.objects.bulk_create([
            Notificacao(usuario_id=pk, tipo=tipo, titulo=titulo[:120], mensagem=mensagem[:255], link=link)
            for pk in destinatarios.values_list('pk', flat=True)
        ])

    transaction.on_commit(_criar)


def nao_lidas(usuario):
    return Notificacao.objects.filter(usuario=usuario, lida_em__isnull=True).count()


def marcar_lidas(usuario, ids=None):
    """Marca como lidas as notificações do `usuario` (todas, ou só `ids`).
    Retorna quantas foram alteradas."""
    qs = Notificacao.objects.filter(usuario=usuario, lida_em__isnull=True)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(lida_em=timezone.now())
