from django.contrib import admin

from .models import Palete, PaleteHistorico


@admin.register(Palete)
class PaleteAdmin(admin.ModelAdmin):
    list_display = ('barcode_palete', 'sku', 'lote', 'peso_kg', 'estado', 'endereco', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('barcode_palete', 'sku', 'lote')


@admin.register(PaleteHistorico)
class PaleteHistoricoAdmin(admin.ModelAdmin):
    list_display = ('palete', 'estado', 'usuario', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('palete__barcode_palete',)
