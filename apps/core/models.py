from django.conf import settings
from django.db import models


class PerfilUsuario(models.Model):
    """Marca usuários cujo grupo (Gestor/Colaborador) foi atribuído manualmente
    por um Administrador (tela de atribuição do AD), em vez de calculado pelo
    mapeamento automático de ldap_signals.py — que hoje dá Operador+Gestor
    juntos para quem está apenas no grupo guarda-chuva do AD (Produção ou
    Logística). Usuário com perfil aqui tem seu grupo preservado no login."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil')
    atribuido_manualmente = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Perfil do Usuário'
        verbose_name_plural = 'Perfil dos Usuários'

    def __str__(self):
        return self.user.username


class Notificacao(models.Model):
    """Notificação in-app para um usuário (sino no topo do shell). Uma linha por
    destinatário; criada por apps/core/notificacoes.py quando uma etapa do fluxo
    passa a depender de quem tem determinada função (grupo)."""

    LOTE_APROVADO_PRODUCAO = 'LOTE_APROVADO_PRODUCAO'
    TIPO_CHOICES = [
        (LOTE_APROVADO_PRODUCAO, 'Lote aprovado pela Produção'),
    ]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notificacoes')
    tipo = models.CharField(max_length=40, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=120)
    mensagem = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    lida_em = models.DateTimeField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-criada_em', '-pk']
        indexes = [models.Index(fields=['usuario', 'lida_em'])]
        verbose_name = 'Notificação'
        verbose_name_plural = 'Notificações'

    def __str__(self):
        return f'{self.usuario} — {self.titulo}'

    @property
    def lida(self):
        return self.lida_em is not None
