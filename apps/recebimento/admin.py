from django.contrib import admin

from .models import Palete, PaleteHistorico


class SomenteLeituraAdmin(admin.ModelAdmin):
    """Estado, endereço e histórico só mudam pelo fluxo da aplicação (que valida
    a máquina de estados e grava o histórico); o admin serve apenas para consulta."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Palete)
class PaleteAdmin(SomenteLeituraAdmin):
    list_display = ('barcode_palete', 'sku', 'lote', 'peso_kg', 'estado', 'endereco', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('barcode_palete', 'sku', 'lote')


@admin.register(PaleteHistorico)
class PaleteHistoricoAdmin(SomenteLeituraAdmin):
    list_display = ('palete', 'estado', 'usuario', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('palete__barcode_palete',)
