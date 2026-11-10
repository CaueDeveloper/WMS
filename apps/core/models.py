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
