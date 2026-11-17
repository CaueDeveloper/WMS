from django.contrib import admin

from .models import Notificacao, PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'atribuido_manualmente')
    list_filter = ('atribuido_manualmente',)


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    list_display = ('criada_em', 'usuario', 'tipo', 'titulo', 'lida_em')
    list_filter = ('tipo', 'lida_em')
    search_fields = ('usuario__username', 'titulo', 'mensagem')
    raw_id_fields = ('usuario',)
